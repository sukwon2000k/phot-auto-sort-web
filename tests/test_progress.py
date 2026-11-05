"""실제 파일 처리 완료 시점과 서버 진행률의 일치를 검증합니다."""

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.sorter import sort_to_zip
from test_web import image_bytes, settings_for, wait_result


def test_live_progress_matches_completed_files(tmp_path):
    """두 번째 추론을 멈춘 동안 첫 사진 하나만 처리돼 1/2·50%인지 확인합니다."""
    entered, release = threading.Event(), threading.Event()
    class Predictor:
        """두 번째 사진을 처리하기 직전에 대기하는 검증용 예측기입니다."""
        calls = 0
        def predict_path(self, path):
            """호출 수를 세며 테스트 신호를 기다린 뒤 food를 반환합니다."""
            self.calls += 1
            if self.calls == 2:
                entered.set()
                assert release.wait(5)
            return "food"
    with TestClient(create_app(settings_for(tmp_path), lambda _: Predictor())) as client:
        response = client.post("/api/jobs", files=[
            ("files", ("a.png", image_bytes())), ("files", ("b.png", image_bytes()))])
        try:
            assert entered.wait(5)
            snapshot = client.get("/api/jobs/" + response.json()["id"]).json()
            assert snapshot["status"] == "processing"
            assert snapshot["progress"] == {
                "phase": "classifying", "processed": 1, "total": 2, "percent": 50}
            assert client.get("/api/jobs/" + response.json()["id"] + "/download").status_code == 409
        finally:
            release.set()
        final = wait_result(client, response.json()["id"])
        assert final["progress"] == {"phase": "completed", "processed": 2, "total": 2, "percent": 100}


def test_failed_file_is_counted_once(tmp_path):
    """검증 실패 사진과 성공 사진이 중복 집계되지 않고 총량에 들어가는지 확인합니다."""
    class Predictor:
        """유효한 사진에 person을 반환하는 검증 대역입니다."""
        def predict_path(self, path):
            """테스트에서 실제 모델 대신 정해진 클래스 하나를 반환합니다."""
            return "person"
    with TestClient(create_app(settings_for(tmp_path), lambda _: Predictor())) as client:
        response = client.post("/api/jobs", files=[
            ("files", ("bad.png", b"broken")), ("files", ("good.png", image_bytes()))])
        final = wait_result(client, response.json()["id"])
        assert final["failed_count"] == 1 and final["counts"]["person"] == 1
        assert final["progress"]["processed"] == final["progress"]["total"] == 2


def test_sorter_reports_progress_after_predict_and_copy(tmp_path):
    """선택적 sorter 콜백은 예측 실패와 성공을 모두 세며 ZIP 단계를 별도로 알립니다."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.png").write_bytes(image_bytes())
    (source / "b.png").write_bytes(image_bytes())
    events = []
    class Predictor:
        """첫 파일은 읽기 실패, 두 번째는 성공으로 처리하는 테스트 대역입니다."""
        def predict_path(self, path):
            """파일명에 따라 실패 또는 food를 반환합니다."""
            if Path(path).name == "a.png":
                raise OSError("unreadable")
            return "food"
    result = sort_to_zip(source, tmp_path / "out", Predictor(), on_progress=lambda *event: events.append(event))
    assert result.failed_count == 1
    assert events == [
        ("classifying", 0, 2), ("classifying", 1, 2),
        ("classifying", 2, 2), ("zipping", 2, 2),
    ]


def test_zip_preparation_is_not_completed_job(tmp_path, monkeypatch):
    """사진 100%와 ZIP 생성 완료 사이에는 다운로드를 허용하지 않는지 확인합니다."""
    import app.main as main_module
    original = main_module.process_job
    entered, release = threading.Event(), threading.Event()

    def wrapped(directory, entries, predictor, settings, notify):
        """실제 처리 함수의 ZIP 시작 콜백에서만 잠시 대기합니다."""
        def progress(phase, processed, total):
            """실제 진행 이벤트를 전달하고 ZIP 준비 상태 관찰 시간을 확보합니다."""
            notify(phase, processed, total)
            if phase == "zipping":
                entered.set()
                assert release.wait(5)
        return original(directory, entries, predictor, settings, progress)

    class Predictor:
        """유효한 사진에 food를 반환하는 테스트 대역입니다."""
        def predict_path(self, path):
            """한 장을 성공으로 처리합니다."""
            return "food"

    monkeypatch.setattr(main_module, "process_job", wrapped)
    with TestClient(create_app(settings_for(tmp_path), lambda _: Predictor())) as client:
        response = client.post("/api/jobs", files={"files": ("one.png", image_bytes())})
        try:
            assert entered.wait(5)
            snapshot = client.get("/api/jobs/" + response.json()["id"]).json()
            assert snapshot["progress"]["phase"] == "zipping"
            assert snapshot["progress"]["percent"] == 100
            assert snapshot["status"] == "processing"
            assert snapshot["download_url"] is None
        finally:
            release.set()
        final = wait_result(client, response.json()["id"])
        assert final["status"] == "completed"
        assert client.get(final["download_url"]).status_code == 200
