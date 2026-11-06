"""완료 사진을 페이지별로 보여 주고 선택한 원본만 하나의 ZIP으로 만듭니다."""

import asyncio
import csv
import io
import math
import uuid
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from fastapi import HTTPException, Request
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

from .chunks import small_json


def read_items(directory):
    """보고서의 성공 행을 ID 목록으로 바꿉니다. 원본 서버 경로는 응답에 넣지 않습니다."""
    items = []
    report = directory / "classification_report.csv"
    with report.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["status"] == "ok":
                path = directory / "sorted_images" / row["destination"]
                items.append({"id": len(items), "name": path.name,
                              "class_name": row["predicted_class"], "size": path.stat().st_size,
                              "destination": row["destination"]})
    return items


def thumbnail(job, item):
    """미리보기용 작은 JPEG만 생성합니다. 다운로드할 원본은 변경하지 않습니다."""
    cache = job.directory / "thumbnails"
    cache.mkdir(exist_ok=True)
    path = cache / f'{item["id"]}.jpg'
    if not path.exists():
        original = job.result_dir / "sorted_images" / item["destination"]
        with Image.open(original) as image:
            image.draft("RGB", (360, 360))
            preview = ImageOps.exif_transpose(image)
            preview.thumbnail((360, 360))
            temp = cache / (uuid.uuid4().hex + ".jpg")
            try:
                preview.convert("RGB").save(temp, "JPEG", quality=78)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
    return path


def selected_zip(job, export, excluded, notify):
    """선택한 사진을 원본 바이트 그대로 ZIP64에 기록하고 실제 기록 바이트로 진행률을 냅니다."""
    chosen = [item for item in job.items if item["id"] not in excluded]
    total = sum(item["size"] for item in chosen)
    done = 0
    temp = export["path"].with_suffix(".part")
    notify(0, total)
    try:
        # JPEG/PNG는 이미 압축돼 있어 재압축 대신 저장하여 CPU 부담을 줄입니다.
        with ZipFile(temp, "w", compression=ZIP_STORED, allowZip64=True) as archive:
            for category in ("landscape", "person", "food"):
                archive.writestr(category + "/", b"")
            destinations = {item["destination"] for item in chosen}
            for item in chosen:
                source = job.result_dir / "sorted_images" / item["destination"]
                with source.open("rb") as src, archive.open(item["destination"], "w", force_zip64=True) as dst:
                    while chunk := src.read(1024**2):
                        dst.write(chunk)
                        done += len(chunk)
                        notify(done, total)
            # 보고서도 선택한 성공 사진과 전체 실패 기록만 포함합니다.
            report = io.StringIO(newline="")
            with (job.result_dir / "classification_report.csv").open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                writer = csv.DictWriter(report, fieldnames=reader.fieldnames)
                writer.writeheader()
                for row in reader:
                    if row["status"] != "ok" or row["destination"] in destinations:
                        writer.writerow(row)
            archive.writestr("classification_report.csv", report.getvalue().encode("utf-8-sig"))
        temp.replace(export["path"])
    finally:
        temp.unlink(missing_ok=True)


def register_review_routes(app, find_job, DownloadResponse):
    """분류별 목록, 썸네일, 선택 ZIP의 비동기 생성·조회·다운로드를 등록합니다."""

    def ready(job_id):
        """완료 결과만 검토할 수 있게 확인합니다."""
        job = find_job(job_id)
        if job.status != "completed":
            raise HTTPException(409, "분류가 끝난 뒤 확인할 수 있습니다.")
        return job

    @app.get("/api/jobs/{job_id}/items")
    async def items(job_id: str, category: str = "landscape", page: int = 0, page_size: int = 48):
        """최대 60장씩 반환해 수만 장을 한 번에 브라우저에 올리지 않습니다."""
        job = ready(job_id)
        if category not in {"landscape", "person", "food"} or page < 0 or not 1 <= page_size <= 60:
            raise HTTPException(400, "분류 또는 페이지가 잘못됐습니다.")
        filtered = [item for item in job.items if item["class_name"] == category]
        subset = filtered[page * page_size:(page + 1) * page_size]
        return {"total": len(filtered), "pages": math.ceil(len(filtered) / page_size), "items": [
            {key: value for key, value in item.items() if key != "destination"} | {
                "thumbnail_url": f'/api/jobs/{job.id}/items/{item["id"]}/thumbnail'} for item in subset]}

    @app.get("/api/jobs/{job_id}/items/{item_id}/thumbnail")
    async def preview(job_id: str, item_id: int):
        """작은 미리보기만 내려주고 전송 중에는 결과 정리를 막습니다."""
        job = ready(job_id)
        if not 0 <= item_id < len(job.items):
            raise HTTPException(404, "사진을 찾지 못했습니다.")
        job.downloads += 1
        try:
            async with app.state.thumbnail_slots:
                path = await run_in_threadpool(thumbnail, job, job.items[item_id])
            return DownloadResponse(job, path=path, filename=None, media_type="image/jpeg")
        finally:
            job.downloads -= 1

    async def build(job, export, excluded):
        """ZIP 쓰기를 스레드로 보내 API 응답을 막지 않습니다."""
        loop = asyncio.get_running_loop()
        def apply(done, total):
            export["progress"] = {"processed": done, "total": total,
                                  "percent": round(done * 100 / total, 1) if total else 100}
        def notify(done, total):
            loop.call_soon_threadsafe(apply, done, total)
        try:
            await run_in_threadpool(selected_zip, job, export, excluded, notify)
            export["status"] = "completed"
        except Exception:
            export["status"] = "failed"
            export["error"] = "선택 ZIP 생성에 실패했습니다. 서버 저장 공간을 확인해 주세요."
        finally:
            job.downloads -= 1

    def public(job, export):
        """내부 경로를 제외한 내보내기 상태를 반환합니다."""
        return {"id": export["id"], "status": export["status"], "progress": export.get("progress"),
                "error": export.get("error"), "download_url":
                f'/api/jobs/{job.id}/exports/{export["id"]}/download' if export["status"] == "completed" else None}

    @app.post("/api/jobs/{job_id}/exports", status_code=202)
    async def export_selected(job_id: str, request: Request):
        """제외 ID 목록만 받아 큰 선택 목록도 작은 JSON 한 번으로 접수합니다."""
        body = await small_json(request)
        job = ready(job_id)
        categories = body.get("categories", ["landscape", "person", "food"])
        included = body.get("include_ids", [])
        excluded = body.get("excluded_ids", [])
        if not isinstance(categories, list) or any(c not in {"landscape", "person", "food"} for c in categories):
            raise HTTPException(400, "선택한 분류가 잘못됐습니다.")
        for ids in (included, excluded):
            if not isinstance(ids, list) or len(ids) > len(job.items) or any(
                type(i) is not int or not 0 <= i < len(job.items) for i in ids
            ):
                raise HTTPException(400, "선택한 사진 목록이 잘못됐습니다.")
        included, manual_excluded = set(included), set(excluded)
        excluded = {item["id"] for item in job.items if
                    (item["class_name"] not in categories and item["id"] not in included)
                    or item["id"] in manual_excluded}
        if len(excluded) == len(job.items):
            raise HTTPException(400, "다운로드할 사진을 한 장 이상 선택해 주세요.")
        if any(e["status"] == "processing" for e in job.exports.values()):
            raise HTTPException(409, "이 작업의 ZIP을 이미 만들고 있습니다.")
        # 최근 결과 3개를 보관합니다. 전송 중인 결과는 삭제하지 않습니다.
        if len(job.exports) >= 3:
            if job.downloads:
                raise HTTPException(409, "다운로드가 끝난 뒤 다시 ZIP을 만들어 주세요.")
            oldest = next(iter(job.exports))
            previous = job.exports.pop(oldest)
            if previous["path"] != job.zip_path:
                previous["path"].unlink(missing_ok=True)
        selected_bytes = sum(i["size"] for i in job.items if i["id"] not in excluded)
        import shutil
        if excluded and shutil.disk_usage(job.directory).free < selected_bytes + app.state.settings.disk_reserve_bytes:
            raise HTTPException(507, "결과 ZIP을 만들 저장 공간이 부족합니다.")
        export_id = uuid.uuid4().hex
        export = {"id": export_id, "status": "processing", "path": job.directory / (export_id + ".zip")}
        job.exports[export_id] = export
        if not excluded:
            export.update(status="completed", path=job.zip_path, progress={"processed": selected_bytes, "total": selected_bytes, "percent": 100})
        else:
            job.downloads += 1
            task = asyncio.create_task(build(job, export, excluded))
            app.state.export_tasks.add(task)
            task.add_done_callback(app.state.export_tasks.discard)
        return public(job, export)

    @app.get("/api/jobs/{job_id}/exports/{export_id}")
    async def export_status(job_id: str, export_id: str):
        """브라우저가 짧은 요청으로 ZIP 생성 진행률을 조회합니다."""
        job = ready(job_id)
        if export_id not in job.exports:
            raise HTTPException(404, "내보내기 결과가 없거나 새 결과로 교체됐습니다.")
        return public(job, job.exports[export_id])

    @app.get("/api/jobs/{job_id}/exports/{export_id}/download")
    async def export_download(job_id: str, export_id: str):
        """ZIP을 직접 전송합니다. 브라우저 fetch().blob()을 사용하지 않습니다."""
        job = ready(job_id)
        export = job.exports.get(export_id)
        if not export or export["status"] != "completed":
            raise HTTPException(409, "다운로드할 ZIP이 준비되지 않았습니다.")
        return DownloadResponse(job, path=export["path"], filename="selected_photos.zip")
