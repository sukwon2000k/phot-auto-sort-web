"""몇천 장의 multipart 업로드를 메모리에 쌓지 않고 파일 하나씩 디스크에 저장합니다."""

from pathlib import Path

from fastapi import HTTPException
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.concurrency import run_in_threadpool

from .web_jobs import safe_filename


class UploadReceiver:
    """multipart 콜백을 통해 파일 수·용량을 제한하며 현재 파일 하나만 열어 둡니다."""

    def __init__(self, directory: Path, settings):
        """작업 경로와 서버 제한값을 받아 스트리밍 수신 상태를 준비합니다."""
        self.directory = directory
        self.settings = settings
        self.entries = []
        self.output = None
        self.done = False
        self.headers = {}
        self.header_name = bytearray()
        self.header_value = bytearray()
        self.size = 0
        self.limit = 0

    def part_begin(self):
        """새 파트의 헤더 상태를 초기화합니다."""
        self.headers = {}
        self.header_name.clear()
        self.header_value.clear()

    def field(self, data, start, end):
        """분할 전달된 헤더 이름 바이트를 모읍니다."""
        self.header_name.extend(data[start:end])

    def value(self, data, start, end):
        """분할 전달된 헤더 값 바이트를 모읍니다."""
        self.header_value.extend(data[start:end])

    def header_end(self):
        """완성된 헤더를 저장합니다. 중복 헤더는 모호한 해석을 막기 위해 거부합니다."""
        key = bytes(self.header_name).lower()
        if key in self.headers:
            raise HTTPException(400, "중복된 업로드 헤더입니다.")
        self.headers[key] = bytes(self.header_value)
        self.header_name.clear()
        self.header_value.clear()

    def headers_finished(self):
        """files 필드인지 검사하고 서버가 정한 경로에 새 파일을 엽니다."""
        disposition, options = parse_options_header(self.headers.get(b"content-disposition"))
        if disposition != b"form-data" or options.get(b"name") != b"files" or b"filename" not in options:
            raise HTTPException(400, "files 필드에 사진 또는 ZIP 파일만 전송해 주세요.")
        if len(self.entries) >= self.settings.max_files:
            raise HTTPException(400, f"업로드 파일이 최대 {self.settings.max_files:,}개를 넘었습니다.")
        raw_name = options[b"filename"]
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            name = raw_name.decode("latin-1")
        name = safe_filename(name or "photo")
        self.limit = self.settings.max_archive_bytes if Path(name).suffix == ".zip" else self.settings.max_file_bytes
        target = self.directory / "uploads" / f"{len(self.entries):06d}" / name
        target.parent.mkdir(parents=True)
        self.output = target.open("xb")
        self.entries.append((target, name))
        self.size = 0

    def data(self, data, start, end):
        """수신 조각의 실제 크기를 누적하고 한도를 넘기기 전에 쓰기를 중단합니다."""
        self.size += end - start
        if self.size > self.limit:
            raise HTTPException(413, "사진 또는 ZIP 파일의 개별 용량 제한을 넘었습니다.")
        self.output.write(data[start:end])

    def part_end(self):
        """현재 파일을 즉시 닫아 수천 개의 파일 핸들이 동시에 열리지 않게 합니다."""
        self.close()

    def end(self):
        """마지막 경계를 수신했음을 기록합니다. 잘린 요청을 성공 접수하지 않습니다."""
        self.done = True

    def close(self):
        """정상 완료·수신 취소·파싱 오류에 관계없이 현재 파일 핸들을 반환합니다."""
        if self.output:
            self.output.close()
            self.output = None

    def callbacks(self):
        """스트리밍 파서에 전달할 이벤트 콜백 매핑을 반환합니다."""
        return {
            "on_part_begin": self.part_begin, "on_header_field": self.field,
            "on_header_value": self.value, "on_header_end": self.header_end,
            "on_headers_finished": self.headers_finished, "on_part_data": self.data,
            "on_part_end": self.part_end, "on_end": self.end,
        }


async def receive_upload(request, directory: Path, settings):
    """실제 요청 바이트를 제한하면서 디스크로 수신하고 (경로, 이름) 목록을 반환합니다."""
    length = request.headers.get("content-length")
    if length:
        try:
            size = int(length)
        except ValueError:
            raise HTTPException(400, "올바르지 않은 요청 크기입니다.") from None
        if size < 0:
            raise HTTPException(400, "올바르지 않은 요청 크기입니다.")
        if size > settings.max_request_bytes:
            raise HTTPException(413, "전체 업로드 용량 제한을 넘었습니다.")
    _, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if not boundary or len(boundary) > 200:
        raise HTTPException(400, "올바른 multipart 경계가 필요합니다.")
    receiver = UploadReceiver(directory, settings)
    parser = MultipartParser(boundary, receiver.callbacks(), max_header_count=8, max_header_size=8192)
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > settings.max_request_bytes:
                raise HTTPException(413, "전체 업로드 용량 제한을 넘었습니다.")
            # 파싱 콜백의 디스크 쓰기를 작업 스레드에서 처리하여 상태 API를 막지 않습니다.
            await run_in_threadpool(parser.write, chunk)
        parser.finalize()
        if not receiver.done or not receiver.entries:
            raise HTTPException(400, "업로드가 완전하지 않거나 파일이 없습니다.")
        return receiver.entries
    except MultipartParseError:
        raise HTTPException(400, "업로드 형식을 읽지 못했습니다.") from None
    finally:
        receiver.close()
