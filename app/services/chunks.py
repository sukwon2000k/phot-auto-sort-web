"""대용량 파일을 작은 HTTP 요청으로 수신하고 백그라운드에서 원본으로 합칩니다."""

import asyncio
import json
import math
import shutil
import time
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .web_jobs import safe_filename


async def small_json(request: Request, limit=4 * 1024**2):
    """목록 JSON도 실제 바이트를 제한해 읽고 잘못된 형식은 400으로 반환합니다."""
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > limit:
            raise HTTPException(413, "파일 목록이 너무 큽니다.")
        data.extend(part)
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "올바른 JSON이 필요합니다.") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "JSON 객체가 필요합니다.")
    return value


@dataclass
class UploadSession:
    """서버가 검증한 파일 목록과 수신된 조각 번호를 관리합니다."""

    files: list
    chunk_bytes: int
    received: set = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def assemble(job, session, notify):
    """조각을 순서대로 디스크에 합칩니다. 전체 파일을 RAM에 올리지 않습니다."""
    total = sum(f["size"] for f in session.files)
    done, entries = 0, []
    notify("merging", 0, total)
    for file_index, item in enumerate(session.files):
        name = item["name"]
        target = job.directory / "uploads" / f"{file_index:06d}" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            for index in range(item["chunks"]):
                path = job.directory / "chunks" / f"{file_index}_{index}"
                with path.open("rb") as source:
                    while block := source.read(1024**2):
                        output.write(block)
                        done += len(block)
                        notify("merging", done, total)
                # 합친 조각은 즉시 지워 임시 저장 공간을 되돌려 줍니다.
                path.unlink()
        if target.stat().st_size != item["size"]:
            raise ValueError("합친 파일의 크기가 일치하지 않습니다.")
        entries.append((target, name))
    shutil.rmtree(job.directory / "chunks", ignore_errors=True)
    return entries


def register_chunk_routes(app, settings, Job, find_job, run_job):
    """기존 앱의 모델·작업 상태와 연결된 분할 업로드 API를 등록합니다."""

    @app.post("/api/uploads", status_code=201)
    async def begin(request: Request):
        """작은 파일 명세만 받아 전체 용량을 확인하고 업로드 슬롯을 예약합니다."""
        body = await small_json(request)
        if app.state.predictor is None:
            raise HTTPException(503, "분류 모델이 준비되지 않았습니다.")
        files = body.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= settings.max_files:
            raise HTTPException(400, "선택한 파일 수를 확인해 주세요.")
        checked, total = [], 0
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise HTTPException(400, "파일 이름이 필요합니다.")
            size = item.get("size")
            if type(size) is not int or size <= 0:
                raise HTTPException(400, "빈 파일이나 잘못된 파일 크기입니다.")
            name = safe_filename(item["name"])
            suffix = name.rsplit(".", 1)[-1]
            if suffix not in {"jpg", "jpeg", "jfif", "png", "webp", "zip"}:
                raise HTTPException(415, "지원하지 않는 파일입니다.")
            limit = settings.max_archive_bytes if suffix == "zip" else settings.max_file_bytes
            if size > limit:
                raise HTTPException(413, "사진 또는 ZIP의 개별 용량 제한을 넘었습니다.")
            total += size
            checked.append({"name": name, "size": size, "chunks": math.ceil(size / settings.chunk_bytes)})
        if total > settings.max_request_bytes:
            raise HTTPException(413, "서버의 전체 업로드 한도를 넘었습니다.")
        if shutil.disk_usage(settings.work_dir).free < total * 2 + settings.disk_reserve_bytes:
            raise HTTPException(507, "서버 저장 공간이 부족합니다. 파일 크기를 줄여 주세요.")
        # await 없는 구간에서 슬롯을 예약해 동시 접수 경합을 막습니다.
        if app.state.busy:
            raise HTTPException(409, "다른 업로드나 분류가 진행 중입니다.")
        app.state.busy = True
        job = Job(uuid.uuid4().hex, settings.work_dir / ("job_" + uuid.uuid4().hex), status="uploading")
        try:
            (job.directory / "chunks").mkdir(parents=True)
            job.session = UploadSession(checked, settings.chunk_bytes)
            job.expires_at = time.time() + settings.upload_timeout
            app.state.jobs[job.id] = job
        except Exception:
            app.state.busy = False
            shutil.rmtree(job.directory, ignore_errors=True)
            raise
        return {"id": job.id, "chunk_bytes": settings.chunk_bytes}

    @app.get("/api/uploads/{job_id}")
    async def state(job_id: str):
        """성공 응답이 유실돼도 서버에 저장된 조각 번호를 다시 확인할 수 있습니다."""
        job = find_job(job_id)
        if job.status != "uploading":
            return {"status": job.status, "received": []}
        return {"status": job.status, "received": sorted([list(pair) for pair in job.session.received])}

    @app.put("/api/uploads/{job_id}/{file_index}/{chunk_index}")
    async def put(job_id: str, file_index: int, chunk_index: int, request: Request):
        """한 조각을 스트리밍 저장합니다. 완료된 번호의 재전송은 안전하게 건너뜁니다."""
        job = find_job(job_id)
        session = job.session
        if session is None:
            raise HTTPException(409, "이미 업로드가 끝났습니다.")
        async with session.lock:
            if job.status != "uploading":
                raise HTTPException(409, "이미 접수된 업로드입니다.")
            if not 0 <= file_index < len(session.files):
                raise HTTPException(400, "파일 번호가 잘못됐습니다.")
            item = session.files[file_index]
            if not 0 <= chunk_index < item["chunks"]:
                raise HTTPException(400, "조각 번호가 잘못됐습니다.")
            if (file_index, chunk_index) in session.received:
                return {"ok": True, "duplicate": True}
            expected = min(session.chunk_bytes, item["size"] - chunk_index * session.chunk_bytes)
            if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
                raise HTTPException(415, "조각은 binary로 전송해야 합니다.")
            path = job.directory / "chunks" / f"{file_index}_{chunk_index}"
            temp = path.with_suffix(".part")
            received = 0
            job.downloads += 1  # 수신 중 정리 작업이 임시 파일을 지우지 않도록 보호합니다.
            try:
                async with asyncio.timeout(settings.upload_timeout):
                    with temp.open("wb") as stream:
                        async for block in request.stream():
                            received += len(block)
                            if received > expected:
                                raise HTTPException(413, "조각 용량이 예상 크기를 넘었습니다.")
                            await run_in_threadpool(stream.write, block)
                if received != expected:
                    raise HTTPException(400, "조각이 끝까지 도착하지 않았습니다. 다시 전송해 주세요.")
                temp.replace(path)
                session.received.add((file_index, chunk_index))
                job.expires_at = time.time() + settings.upload_timeout
                return {"ok": True}
            except TimeoutError:
                raise HTTPException(408, "업로드 시간이 초과됐습니다.") from None
            finally:
                job.downloads -= 1
                temp.unlink(missing_ok=True)

    @app.post("/api/uploads/{job_id}/complete", status_code=202)
    async def complete(job_id: str):
        """누락을 확인한 후 합치기와 분류를 비동기로 시작해 즉시 작업 ID를 반환합니다."""
        job = find_job(job_id)
        if job.status != "uploading":
            return job.public()
        session = job.session
        async with session.lock:
            if job.status != "uploading":
                return job.public()  # 접수 응답 유실 후 재호출해도 분류를 두 번 실행하지 않습니다.
            if len(session.received) != sum(item["chunks"] for item in session.files):
                raise HTTPException(409, "도착하지 않은 조각이 있습니다. 업로드를 이어서 진행해 주세요.")
            job.status, job.expires_at = "processing", None
            app.state.runner = asyncio.create_task(run_job(app, job, None))
            return job.public()

    @app.delete("/api/uploads/{job_id}")
    async def cancel(job_id: str):
        """시작 전 업로드만 취소하고 슬롯과 디스크를 반환합니다."""
        job = find_job(job_id)
        if job.session is None:
            raise HTTPException(409, "이미 분류가 시작됐습니다.")
        async with job.session.lock:
            if job.status != "uploading":
                raise HTTPException(409, "이미 분류가 시작됐습니다.")
            del app.state.jobs[job_id]
            await run_in_threadpool(shutil.rmtree, job.directory, True)
            app.state.busy = False
        return {"ok": True}
