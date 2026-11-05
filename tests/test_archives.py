"""ZIP 입력과 수천 장 처리·스트리밍 업로드를 테스트 전용 예측기로 검증합니다."""

import asyncio
import io
import stat
import struct
import time
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import create_app
from app.services.uploads import receive_upload
from test_web import TestPredictor, image_bytes, settings_for, wait_result


def archive_bytes(items):
    """(파일명 또는 ZipInfo, 바이트) 목록으로 ZIP 테스트 원본을 만듭니다."""
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in items:
            archive.writestr(name, data)
    return output.getvalue()


def test_nested_folders_and_mixed_uploads(tmp_path):
    """개별 사진과 여러 ZIP·중복 이름·손상 사진·미지원 파일의 혼합 입력을 검증합니다."""
    red = image_bytes()
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files=[
            ("files", ("one.zip", archive_bytes([
                ("a/same.png", red), ("b/same.png", red), ("b/bad.png", b"broken"),
                ("nested.zip", archive_bytes([])), ("notes.txt", b"text"),
                ("__MACOSX/._same.png", b"metadata"), (".DS_Store", b"metadata"),
            ]))),
            ("files", ("two.zip", archive_bytes([("more/same.png", red)]))),
            ("files", ("single.png", red)),
        ])
        assert response.status_code == 202
        job = wait_result(client, response.json()["id"])
        assert job["status"] == "completed"
        assert job["counts"]["food"] == 4 and job["failed_count"] == 3
        with ZipFile(io.BytesIO(client.get(job["download_url"]).content)) as archive:
            for name in ("same.png", "same_2.png", "same_3.png", "single.png"):
                assert archive.read("food/" + name) == red
            report = archive.read("classification_report.csv").decode("utf-8-sig")
            assert "one.zip / a/same.png" in report
            assert "one.zip / b/same.png" in report


def test_archive_traversal_links_and_corruption(tmp_path):
    """ZIP 경로 탈출·심볼릭 링크·손상된 ZIP이 정상 사진을 막지 않는지 확인합니다."""
    symlink = ZipInfo("link.png")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files=[
            ("files", ("safe.zip", archive_bytes([
                ("../outside.png", image_bytes()), ("/absolute.png", image_bytes()),
                ("C:/windows.png", image_bytes()), (symlink, b"../../outside"),
                ("normal.png", image_bytes()),
            ]))),
            ("files", ("broken.zip", b"not a zip")),
        ])
        job = wait_result(client, response.json()["id"])
        assert job["counts"]["food"] == 1 and job["failed_count"] == 5
        assert not (tmp_path / "outside.png").exists()


@pytest.mark.parametrize("changes", [
    {"max_files": 1}, {"max_expanded_bytes": 100}, {"max_archive_entries": 1},
])
def test_archive_global_limits_fail_clearly(tmp_path, changes):
    """사진 수·압축 해제 총량·ZIP 목록 상한 초과 시 오류와 슬롯 반환을 확인합니다."""
    with TestClient(create_app(settings_for(tmp_path, **changes), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files={
            "files": ("photos.zip", archive_bytes([("a.png", image_bytes()), ("b.png", image_bytes())]))})
        job = wait_result(client, response.json()["id"])
        assert job["status"] == "failed"
        assert "넘었" in job["error"] or "제한" in job["error"]
        assert not client.get("/api/status").json()["busy"]
        assert not list((tmp_path / "work").glob("job_*"))


def test_member_limit_and_archive_upload_size(tmp_path):
    """ZIP 개별 용량 제한과 압축 해제된 사진별 용량 제한을 서로 구분합니다."""
    with TestClient(create_app(settings_for(tmp_path, max_archive_bytes=100), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files={"files": ("photos.zip", b"z" * 101)})
        assert response.status_code == 413
    with TestClient(create_app(settings_for(tmp_path, max_file_bytes=100), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files={
            "files": ("photos.zip", archive_bytes([("huge.png", b"x" * 101), ("ok.png", image_bytes())]))})
        job = wait_result(client, response.json()["id"])
        assert job["counts"]["food"] == 1 and job["failed_count"] == 1


def test_encrypted_and_unsupported_archive_entries(tmp_path):
    """암호화 항목과 ZIP64 끝 정보는 실행하지 않고 실패 보고서에 남깁니다."""
    encrypted = bytearray(archive_bytes([("a.png", image_bytes())]))
    # 테스트 목적의 암호화 플래그만 설정합니다. 실제 암호화 구현을 사용하지 않습니다.
    local = encrypted.index(b"PK\x03\x04")
    central = encrypted.index(b"PK\x01\x02")
    struct.pack_into("<H", encrypted, local + 6, struct.unpack_from("<H", encrypted, local + 6)[0] | 1)
    struct.pack_into("<H", encrypted, central + 8, struct.unpack_from("<H", encrypted, central + 8)[0] | 1)
    zip64 = bytearray(archive_bytes([]))
    struct.pack_into("<HH", zip64, 8, 65535, 65535)
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files=[
            ("files", ("encrypted.zip", bytes(encrypted))), ("files", ("zip64.zip", bytes(zip64)))])
        job = wait_result(client, response.json()["id"])
        assert job["status"] == "completed" and job["failed_count"] == 2


def test_truncated_stream_rejected(tmp_path):
    """끝 경계가 잘린 multipart 요청을 완전한 업로드로 접수하지 않습니다."""
    settings = settings_for(tmp_path)
    body = b'--bound\r\nContent-Disposition: form-data; name="files"; filename="x.png"\r\n\r\nabc'
    async def run():
        """작은 조각으로 전달된 ASGI 요청을 만들어 수신기의 완료 검사를 실행합니다."""
        chunks = iter([body[i:i + 3] for i in range(0, len(body), 3)])
        async def receive():
            """요청 본문을 반복자에서 한 조각씩 반환합니다."""
            piece = next(chunks, b"")
            return {"type": "http.request", "body": piece, "more_body": bool(piece)}
        request = Request({"type": "http", "headers": [
            (b"content-type", b"multipart/form-data; boundary=bound")]}, receive)
        with pytest.raises(HTTPException) as error:
            await receive_upload(request, tmp_path, settings)
        assert error.value.status_code == 400
    asyncio.run(run())


def test_two_thousand_photos_in_zip(tmp_path):
    """2,000장 ZIP의 접수·분류·보고서·다운로드를 실제 파일 처리로 검증합니다."""
    photo = image_bytes()
    payload = archive_bytes([(f"photos/{index:05d}.png", photo) for index in range(2000)])
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files={"files": ("2000-photos.zip", payload)})
        assert response.status_code == 202
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = client.get("/api/jobs/" + response.json()["id"]).json()
            if job["status"] != "processing":
                break
            time.sleep(.02)
        assert job["status"] == "completed", job
        assert job["counts"]["food"] == 2000 and job["failed_count"] == 0
        with ZipFile(io.BytesIO(client.get(job["download_url"]).content)) as archive:
            images = [name for name in archive.namelist() if name.endswith(".png")]
            assert len(images) == 2000
            assert archive.read(images[0]) == photo and archive.read(images[-1]) == photo
            assert len(archive.read("classification_report.csv").decode("utf-8-sig").splitlines()) == 2001


def test_two_thousand_individual_uploads(tmp_path):
    """ZIP 없이 2,000개 multipart 파일을 순차 저장·처리하는 경로도 검증합니다."""
    photo = image_bytes()
    with TestClient(create_app(settings_for(tmp_path), lambda _: TestPredictor())) as client:
        response = client.post("/api/jobs", files=[
            ("files", (f"{index:05d}.png", photo)) for index in range(2000)])
        assert response.status_code == 202
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = client.get("/api/jobs/" + response.json()["id"]).json()
            if job["status"] != "processing":
                break
            time.sleep(.02)
        assert job["status"] == "completed"
        assert job["counts"]["food"] == 2000 and job["failed_count"] == 0
