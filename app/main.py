"""사진 업로드, 작업 조회, ZIP 다운로드와 한국어 화면을 제공하는 FastAPI 앱입니다."""

import asyncio
import logging
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .services.web_jobs import EXTENSIONS, InputLimitError, ProcessLock, process_job
from .services.uploads import receive_upload

logger = logging.getLogger("photo_sorter")
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@dataclass
class Job:
    """요청별 작업 상태와 다운로드 만료·사용 중 개수를 메모리에 보관합니다."""

    id: str
    directory: Path
    status: str = "processing"
    counts: dict = field(default_factory=dict)
    failed_count: int = 0
    error: str | None = None
    expires_at: float | None = None
    zip_path: Path | None = None
    downloads: int = 0
    progress: dict = field(default_factory=lambda: {
        "phase": "preparing", "processed": 0, "total": None, "percent": None,
    })

    def public(self):
        """사용자에게 필요한 상태만 반환하고 서버 파일 경로는 공개하지 않습니다."""
        return {
            "id": self.id, "status": self.status, "counts": self.counts,
            "failed_count": self.failed_count, "error": self.error,
            "expires_at": self.expires_at,
            "progress": self.progress,
            "download_url": f"/api/jobs/{self.id}/download" if self.status == "completed" else None,
        }


class DownloadResponse(FileResponse):
    """응답이 끝나거나 연결이 끊길 때까지 정리 대상에서 ZIP을 보호합니다."""

    def __init__(self, job: Job):
        """다운로드 작업을 받아 사용 중 개수를 증가시키고 파일 응답을 만듭니다."""
        super().__init__(
            job.zip_path, filename="sorted_photos.zip", media_type="application/zip",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        self.job = job
        self.job.downloads += 1

    async def __call__(self, scope, receive, send):
        """전송·범위 응답·연결 오류에 관계없이 다운로드 보호를 해제합니다."""
        try:
            # 서버의 경로 전송 확장을 사용하지 않고 실제 바이트 전송 종료까지 보호합니다.
            scope = dict(scope)
            scope["extensions"] = {
                key: value for key, value in scope.get("extensions", {}).items()
                if key != "http.response.pathsend"
            }
            await super().__call__(scope, receive, send)
        finally:
            self.job.downloads -= 1


def load_predictor(settings: Settings):
    """서버 관리자가 배치한 모델을 한 번 로드합니다. 없는 경우 torch도 가져오지 않습니다."""
    if not settings.model_path.is_file():
        raise FileNotFoundError("모델 파일을 배치해야 합니다.")
    from .services.predictor import PhotoPredictor
    return PhotoPredictor(settings.model_path, device=settings.device)


def create_app(settings: Settings | None = None, predictor_loader: Callable = load_predictor):
    """설정과 모델 로더를 받아 앱을 만듭니다. 로더 주입은 자동 검증에서만 사용합니다."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """시작 시 모델을 준비하고 정리 작업을 실행합니다. 종료 시 진행 중 분류를 기다립니다."""
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        lock = ProcessLock(settings.work_dir / ".server.lock")
        lock.acquire()
        app.state.settings = settings
        app.state.jobs = {}
        app.state.busy = False
        app.state.runner = None
        app.state.predictor = None
        stop = asyncio.Event()
        cleaner = None
        try:
            # 작업 메타데이터는 메모리 전용입니다. 재시작 시 이전 링크를 폐기하고 잔여 사진을 지웁니다.
            for directory in settings.work_dir.glob("job_*"):
                if directory.is_dir() and not directory.is_symlink():
                    await run_in_threadpool(shutil.rmtree, directory)
            try:
                app.state.predictor = await run_in_threadpool(predictor_loader, settings)
            except Exception:
                logger.exception("모델을 불러오지 못했습니다. MODEL_PATH와 체크포인트 계약을 확인하세요.")
            cleaner = asyncio.create_task(cleanup_loop(app, stop))
            yield
        finally:
            stop.set()
            if cleaner:
                await cleaner
            if app.state.runner:
                await app.state.runner
            app.state.predictor = None
            lock.release()

    app = FastAPI(title="Photo Auto Sorter", lifespan=lifespan)
    # Vite 빌드 결과를 같은 서버에서 제공하므로 로컬에서는 CORS 설정이 필요하지 않습니다.
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="assets")

    @app.get("/")
    @app.get("/guide")
    @app.get("/processing")
    @app.get("/result")
    async def index():
        """모델이 준비되지 않았어도 웹 화면을 반환합니다."""
        index_path = FRONTEND_DIST / "index.html"
        if not index_path.is_file():
            return HTMLResponse(
                '<html lang="ko"><meta charset="utf-8"><h1>화면 빌드가 필요합니다.</h1>'
                '<p>frontend 폴더에서 npm ci 후 npm run build를 실행해 주세요.</p></html>',
                status_code=503,
            )
        return FileResponse(index_path, headers={"Cache-Control": "no-cache"})

    @app.get("/api/status")
    async def status():
        """모델 준비 여부, 현재 처리 상태, 화면에 표시할 서버 제한값을 반환합니다."""
        ready = app.state.predictor is not None
        return {
            "model_ready": ready, "busy": app.state.busy,
            "message": "사진을 선택해 주세요." if ready else "분류 기능 준비 중입니다. 잠시 후 다시 확인해 주세요.",
            "limits": {
                "extensions": list(EXTENSIONS), "max_files": settings.max_files,
                "max_file_bytes": settings.max_file_bytes,
                "max_request_bytes": settings.max_request_bytes,
                "archive_extensions": [".zip"],
                "max_archive_bytes": settings.max_archive_bytes,
                "max_expanded_bytes": settings.max_expanded_bytes,
                "max_archive_entries": settings.max_archive_entries,
                "upload_timeout_seconds": settings.upload_timeout,
                "retention_seconds": settings.retention_seconds,
                "max_pixels": settings.max_pixels,
            },
        }

    @app.post("/api/jobs", status_code=202)
    async def upload(request: Request):
        """요청을 제한 크기까지 읽고 사진을 저장한 후, 하나의 비동기 분류 작업을 시작합니다."""
        if app.state.predictor is None:
            raise HTTPException(503, "분류 모델이 준비되지 않았습니다. 서버 관리자에게 문의하세요.")
        # await 전에 예약하므로 동시에 들어온 요청도 추론 슬롯 하나만 얻습니다.
        if app.state.busy:
            raise HTTPException(409, "다른 사진을 처리 중입니다. 완료 후 다시 시도하세요.")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "multipart/form-data":
            raise HTTPException(415, "사진은 multipart/form-data로 전송해 주세요.")
        app.state.busy = True
        job = Job(uuid.uuid4().hex, settings.work_dir / ("job_" + uuid.uuid4().hex))
        submitted = False
        try:
            job.directory.mkdir()
            try:
                # 헤더가 없거나 잘못되어도 실제 받은 바이트를 별도로 세므로 우회되지 않습니다.
                async with asyncio.timeout(settings.upload_timeout):
                    entries = await receive_upload(request, job.directory, settings)
            except TimeoutError:
                raise HTTPException(408, "업로드 시간이 초과됐습니다. 파일 수를 줄여 다시 시도하세요.") from None
            app.state.jobs[job.id] = job
            app.state.runner = asyncio.create_task(run_job(app, job, entries))
            submitted = True
            return job.public()
        finally:
            if not submitted:
                shutil.rmtree(job.directory, ignore_errors=True)
                app.state.busy = False

    def find_job(job_id: str):
        """메모리에 등록된 유효한 작업만 찾습니다. 사용자 입력을 파일 경로로 사용하지 않습니다."""
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "작업을 찾지 못했습니다. 서버가 재시작됐거나 보관 기간이 끝났습니다.")
        if job.expires_at is not None and time.time() >= job.expires_at:
            raise HTTPException(410, "보관 기간이 끝났습니다. 사진을 다시 업로드해 주세요.")
        return job

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        """분류 중·완료·실패 상태를 반환합니다. 가짜 진행률은 제공하지 않습니다."""
        return find_job(job_id).public()

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str):
        """완료된 ZIP을 내려줍니다. 만료 전 시작한 다운로드는 전송 종료까지 보호합니다."""
        job = find_job(job_id)
        if job.status != "completed":
            raise HTTPException(409, "아직 다운로드할 결과가 없습니다.")
        return DownloadResponse(job)

    return app


async def run_job(app: FastAPI, job: Job, entries: list):
    """별도 스레드에서 검증·추론·ZIP 생성을 실행하고 작업 상태를 갱신합니다."""
    loop = asyncio.get_running_loop()

    def apply_progress(phase, processed, total):
        """이벤트 루프에서 완성된 진행 스냅샷을 교체해 상태 조회 중 불일치를 막습니다."""
        if job.status != "processing":
            return
        percent = None
        if phase in {"classifying", "zipping"} and total:
            percent = 100 if processed >= total else min(99.9, round(processed * 100 / total, 1))
        job.progress = {"phase": phase, "processed": processed, "total": total, "percent": percent}

    def report_progress(phase, processed, total):
        """추론 스레드에서 실제 파일 처리 완료 이벤트를 서버 루프로 전달합니다."""
        loop.call_soon_threadsafe(apply_progress, phase, processed, total)

    try:
        job.zip_path, job.counts, job.failed_count = await run_in_threadpool(
            process_job, job.directory, entries, app.state.predictor, app.state.settings, report_progress)
        job.status = "completed"
        total = sum(job.counts.values()) + job.failed_count
        job.progress = {"phase": "completed", "processed": total, "total": total, "percent": 100}
    except Exception as error:
        logger.exception("사진 분류 작업 실패: %s", job.id)
        job.error = str(error) if isinstance(error, InputLimitError) else "분류 처리에 실패했습니다. 파일 수를 줄여 재시도하거나 서버 관리자에게 문의하세요."
        await run_in_threadpool(shutil.rmtree, job.directory, True)
        job.status = "failed"
    finally:
        job.expires_at = time.time() + app.state.settings.retention_seconds
        app.state.busy = False


async def cleanup_once(app: FastAPI):
    """만료됐고 다운로드 중이 아닌 작업만 제거합니다."""
    now = time.time()
    for job_id, job in list(app.state.jobs.items()):
        if job.expires_at is not None and now >= job.expires_at and job.downloads == 0:
            # 디렉터리 제거 동안에도 다른 요청에서 다시 다운로드하지 못하게 먼저 등록을 해제합니다.
            del app.state.jobs[job_id]
            try:
                await run_in_threadpool(shutil.rmtree, job.directory)
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception("만료 작업 정리 실패: %s", job_id)
                app.state.jobs[job_id] = job


async def cleanup_loop(app: FastAPI, stop: asyncio.Event):
    """정해진 주기로 자동 정리를 반복하며 종료 신호를 받으면 즉시 끝냅니다."""
    while not stop.is_set():
        await cleanup_once(app)
        try:
            await asyncio.wait_for(stop.wait(), app.state.settings.cleanup_interval)
        except TimeoutError:
            pass


app = create_app()
