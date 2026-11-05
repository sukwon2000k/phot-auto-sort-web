"""실제 체크포인트 없이 웹의 파일 처리·상태·제한·정리 정책을 검증합니다."""

import asyncio
import csv
import io
import threading
import time
import re
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import PROJECT_ROOT, Settings, project_path
from app.main import DownloadResponse, Job, cleanup_once, create_app
from app.services.web_jobs import ProcessLock, safe_filename


def image_bytes(color=(255, 0, 0), fmt="PNG"):
    """지정한 색과 형식의 작은 검증용 이미지 원본 바이트를 반환합니다."""
    output = io.BytesIO()
    Image.new("RGB", (12, 12), color).save(output, format=fmt)
    return output.getvalue()


class TestPredictor:
    """웹 검증 전용 대역입니다. 운영 앱에 연결하거나 실제 모델 결과라고 표시하지 않습니다."""

    __test__ = False

    def predict_path(self, path):
        """파일의 테스트 색상을 읽어 세 분류 중 하나를 반환합니다."""
        with Image.open(path) as image:
            color = image.convert("RGB").getpixel((0, 0))
        return ("food", "landscape", "person")[color.index(max(color))]


def settings_for(tmp_path, **changes):
    """서로 격리된 테스트 작업 폴더와 설정을 반환합니다."""
    return Settings(work_dir=tmp_path / "work", model_path=tmp_path / "missing.pth", **changes)


def wait_result(client, job_id):
    """테스트 작업이 끝날 때까지 짧게 조회하고 최종 응답을 반환합니다."""
    for _ in range(200):
        response = client.get("/api/jobs/" + job_id)
        if response.json()["status"] != "processing":
            return response.json()
        time.sleep(.01)
    pytest.fail("테스트 작업이 완료되지 않았습니다.")


def test_without_model_and_load_failure(tmp_path):
    """모델 부재·로더 실패에서도 화면과 상태 API가 열리고 분류는 503인지 확인합니다."""
    for loader in (None, lambda _: (_ for _ in ()).throw(ValueError("bad checkpoint"))):
        app = create_app(settings_for(tmp_path), **({"predictor_loader": loader} if loader else {}))
        with TestClient(app) as client:
            assert client.get("/").status_code == 200
            for path in ("/guide", "/processing", "/result"):
                assert client.get(path).status_code == 200
            index = client.get("/").text
            asset = re.search(r'src="(/assets/[^"]+\.js)"', index)
            assert asset and client.get(asset.group(1)).status_code == 200
            assert client.get("/api/status").json()["model_ready"] is False
            assert client.post("/api/jobs", files={"files": ("x.png", image_bytes())}).status_code == 503


def test_zip_bytes_names_failures_and_reuse(tmp_path):
    """세 폴더·중복 이름·원본 바이트·손상 파일 보고서와 로더 1회 호출을 검증합니다."""
    calls = []
    def loader(settings):
        """호출 횟수를 기록하고 테스트 예측기를 반환합니다."""
        calls.append(settings)
        return TestPredictor()
    with TestClient(create_app(settings_for(tmp_path), loader)) as client:
        red, green, blue = image_bytes(), image_bytes((0, 255, 0)), image_bytes((0, 0, 255))
        response = client.post("/api/jobs", files=[
            ("files", ("same.png", red)), ("files", ("same.png", red)),
            ("files", ("green.png", green)), ("files", ("../../blue.png", blue)),
            ("files", ("broken.jpg", b"bad")), ("files", ("wrong.gif", image_bytes(fmt="GIF"))),
            ("files", ("fake.png", image_bytes(fmt="BMP"))),
        ])
        assert response.status_code == 202
        result = wait_result(client, response.json()["id"])
        assert result["counts"] == {"food": 2, "landscape": 1, "person": 1}
        assert result["failed_count"] == 3
        with ZipFile(io.BytesIO(client.get(result["download_url"]).content)) as archive:
            assert set(archive.namelist()) == {
                "food/", "landscape/", "person/", "food/same.png", "food/same_2.png",
                "landscape/green.png", "person/blue.png", "classification_report.csv"}
            assert archive.read("food/same.png") == red
            assert archive.read("landscape/green.png") == green
            assert archive.read("person/blue.png") == blue
            rows = list(csv.DictReader(io.StringIO(archive.read("classification_report.csv").decode("utf-8-sig"))))
            assert len(rows) == 7
            assert len([row for row in rows if row["status"] == "failed" and row["error"]]) == 3
        # 처리 후에는 ZIP만 남고 업로드 원본이 삭제됐는지 확인합니다.
        job = client.app.state.jobs[result["id"]]
        assert sorted(path.name for path in job.directory.iterdir()) == ["sorted_photos.zip"]
        second = client.post("/api/jobs", files={"files": ("again.png", red)})
        assert wait_result(client, second.json()["id"])["status"] == "completed"
        assert len(calls) == 1


def test_all_invalid_still_has_report(tmp_path):
    """전부 실패해도 빈 세 폴더와 실패 보고서를 다운로드하는지 확인합니다."""
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files={"files": ("bad.jpg", b"broken")})
        job = wait_result(client, response.json()["id"])
        assert job["failed_count"] == 1 and sum(job["counts"].values()) == 0
        with ZipFile(io.BytesIO(client.get(job["download_url"]).content)) as archive:
            assert set(archive.namelist()) == {"food/", "landscape/", "person/", "classification_report.csv"}


def test_supported_image_formats_and_truncated_file(tmp_path):
    """실제 JPEG·JFIF·PNG·WEBP 원본과 잘린 이미지를 함께 검증합니다."""
    originals = {
        "photo.jpg": image_bytes(fmt="JPEG"),
        "photo.jpeg": image_bytes(fmt="JPEG"),
        "photo.jfif": image_bytes(fmt="JPEG"),
        "photo.png": image_bytes(fmt="PNG"),
        "photo.webp": image_bytes(fmt="WEBP"),
    }
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files=[
            *(("files", (name, content)) for name, content in originals.items()),
            ("files", ("truncated.png", originals["photo.png"][:45])),
        ])
        job = wait_result(client, response.json()["id"])
        assert sum(job["counts"].values()) == 5 and job["failed_count"] == 1
        with ZipFile(io.BytesIO(client.get(job["download_url"]).content)) as archive:
            for name, content in originals.items():
                assert archive.read("food/" + name) == content


def test_limits_and_rejected_upload_cleanup(tmp_path):
    """파일 수·개별 크기·전체 요청·빈 파일·픽셀 제한과 거부 후 슬롯 반환을 검증합니다."""
    with TestClient(create_app(settings_for(tmp_path, max_files=1, max_file_bytes=100), lambda _: TestPredictor())) as client:
        assert client.post("/api/jobs", files=[("files", ("a.png", b"x")), ("files", ("b.png", b"x"))]).status_code == 400
        assert client.post("/api/jobs", files={"files": ("a.png", b"x" * 101)}).status_code == 413
        assert client.post("/api/jobs", files={}).status_code == 415
        assert not list((tmp_path / "work").glob("job_*"))
        response = client.post("/api/jobs", files={"files": ("a.png", b"")})
        assert wait_result(client, response.json()["id"])["failed_count"] == 1
    with TestClient(create_app(settings_for(tmp_path, max_request_bytes=200), lambda _: TestPredictor())) as client:
        assert client.post("/api/jobs", files={"files": ("a.png", b"x" * 201)}).status_code == 413
        # Content-Length 없는 청크 전송에도 실제 요청 본문 제한을 적용합니다.
        response = client.post("/api/jobs", content=iter([b"x" * 150, b"x" * 100]),
                               headers={"content-type": "multipart/form-data; boundary=test"})
        assert response.status_code == 413
    with TestClient(create_app(settings_for(tmp_path, max_pixels=100), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files={"files": ("a.png", image_bytes())})
        assert wait_result(client, response.json()["id"])["failed_count"] == 1


def test_busy_and_inference_failure(tmp_path):
    """추론 중 두 번째 작업을 거부하고 추론 오류를 성공 결과로 표시하지 않는지 확인합니다."""
    entered, release = threading.Event(), threading.Event()
    class BlockingPredictor:
        """동시 요청 시 슬롯 점유를 관찰하기 위한 테스트 대역입니다."""
        def predict_path(self, path):
            """호출 사실을 알린 후 테스트 신호까지 대기합니다."""
            entered.set()
            assert release.wait(5)
            raise RuntimeError("GPU failure")
    with TestClient(create_app(settings_for(tmp_path), lambda _: BlockingPredictor())) as client:
        first = client.post("/api/jobs", files={"files": ("a.png", image_bytes())})
        try:
            assert entered.wait(5)
            assert client.get("/api/status").json()["busy"]
            assert client.post("/api/jobs", files={"files": ("b.png", image_bytes())}).status_code == 409
            assert client.get("/api/jobs/" + first.json()["id"] + "/download").status_code == 409
        finally:
            release.set()
        job = wait_result(client, first.json()["id"])
        assert job["status"] == "failed" and job["download_url"] is None
        assert not client.get("/api/status").json()["busy"]


def test_retention_cleanup_and_active_download(tmp_path):
    """만료·정리와 다운로드 보호가 같은 정책으로 동작하는지 검증합니다."""
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files={"files": ("a.png", image_bytes())})
        result = wait_result(client, response.json()["id"])
        job = client.app.state.jobs[result["id"]]
        job.downloads = 1
        job.expires_at = time.time() - 1
        assert client.get(result["download_url"]).status_code == 410
        client.portal.call(cleanup_once, client.app)
        assert job.zip_path.exists()
        job.downloads = 0
        client.portal.call(cleanup_once, client.app)
        assert not job.directory.exists()
        assert client.get(result["download_url"]).status_code == 404


def test_download_lease_on_disconnect(tmp_path):
    """다운로드 전송 중 오류가 발생해도 보호 개수가 반환되는지 확인합니다."""
    path = tmp_path / "result.zip"
    path.write_bytes(b"zip")
    job = Job("test", tmp_path, zip_path=path)
    response = DownloadResponse(job)
    assert job.downloads == 1
    async def run():
        """ASGI 전송 오류를 일으켜 finally 보호 해제를 확인합니다."""
        async def send(message):
            """클라이언트 연결 종료를 흉내 냅니다."""
            raise OSError("disconnected")
        async def receive():
            """기본 ASGI 요청 메시지를 반환합니다."""
            return {"type": "http.request"}
        with pytest.raises(OSError):
            await response({"type": "http", "method": "GET", "headers": [],
                            "asgi": {"spec_version": "2.4"},
                            "extensions": {"http.response.pathsend": {}}}, receive, send)
    asyncio.run(run())
    assert job.downloads == 0


def test_paths_restart_and_process_lock(tmp_path):
    """상대 모델 경로·파일명 보호·재시작 정리·중복 프로세스 잠금을 확인합니다."""
    assert project_path("models/x.pth") == PROJECT_ROOT / "models/x.pth"
    assert safe_filename(r"C:\secret\CON.jpg") == "_CON.jpg"
    assert "/" not in safe_filename("../../evil.png")
    assert safe_filename("=cmd.png").startswith("_")
    settings = settings_for(tmp_path)
    settings.work_dir.mkdir()
    stale = settings.work_dir / "job_stale"
    stale.mkdir()
    (stale / "photo.png").write_bytes(b"private")
    with TestClient(create_app(settings, lambda _: TestPredictor())):
        assert not stale.exists()
        lock = ProcessLock(settings.work_dir / ".server.lock")
        with pytest.raises(RuntimeError):
            lock.acquire()
