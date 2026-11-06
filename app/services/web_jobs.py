"""웹 업로드 검증과 기존 sorter 연결을 담당합니다. 전처리는 predictor에 맡깁니다."""

import re
import shutil
import stat
import struct
import warnings
import zlib
import lzma
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from PIL import Image

from .sorter import sort_to_zip

EXTENSIONS = (".jpg", ".jpeg", ".jfif", ".png", ".webp")
FORMATS = {"JPEG", "PNG", "WEBP"}


class InputLimitError(ValueError):
    """파일 수·압축 해제 용량 제한 초과를 사용자에게 알려 작업을 중단합니다."""


def inspect_zip_metadata(path: Path, max_entries: int) -> None:
    """중앙 디렉터리를 읽기 전에 ZIP 목록의 크기를 검사하여 메모리 사용량을 제한합니다."""
    # 일반 ZIP의 끝 레코드는 파일 끝에서 최대 65,557바이트 이내에 있습니다.
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 65557))
        tail = stream.read()
    position = len(tail)
    while True:
        position = tail.rfind(b"PK\x05\x06", 0, position)
        if position < 0:
            raise BadZipFile("ZIP 끝 정보를 읽을 수 없습니다.")
        if len(tail) - position >= 22:
            record = struct.unpack_from("<4s4H2IH", tail, position)
            if position + 22 + record[7] == len(tail):
                break
    if record[1] or record[2] or record[3] != record[4]:
        raise BadZipFile("분할 ZIP은 지원하지 않습니다.")
    count, directory_size = record[4], record[5]
    if count == 65535 or directory_size == 0xFFFFFFFF or record[6] == 0xFFFFFFFF:
        # 4GB 이상의 일반 ZIP64도 읽되 다중 볼륨과 과도한 목록은 거부합니다.
        absolute_end = path.stat().st_size - len(tail) + position
        if absolute_end < 20:
            raise BadZipFile("ZIP64 위치 정보가 없습니다.")
        with path.open("rb") as source:
            source.seek(absolute_end - 20)
            locator = source.read(20)
            if len(locator) != 20 or locator[:4] != b"PK\x06\x07":
                raise BadZipFile("ZIP64 위치 정보를 읽을 수 없습니다.")
            _, disk, offset, disks = struct.unpack("<4sIQI", locator)
            if disk or disks != 1:
                raise BadZipFile("다중 볼륨 ZIP은 지원하지 않습니다.")
            source.seek(offset)
            header = source.read(56)
            if len(header) != 56 or header[:4] != b"PK\x06\x06":
                raise BadZipFile("ZIP64 헤더가 잘못됐습니다.")
            values = struct.unpack("<4sQ2H2I4Q", header)
            if values[4] or values[5] or values[6] != values[7]:
                raise BadZipFile("다중 볼륨 ZIP은 지원하지 않습니다.")
            count, directory_size = values[7], values[8]
    if count > max_entries:
        raise InputLimitError(f"ZIP 내부 항목 수가 제한({max_entries:,}개)을 넘었습니다.")
    if directory_size > 32 * 1024**2:
        raise InputLimitError("ZIP 파일 목록 정보가 너무 큽니다. 여러 ZIP으로 나누어 주세요.")


def expand_uploads(job_dir: Path, uploads: list, settings, on_progress=None):
    """ZIP과 개별 사진을 검증용 입력 목록으로 바꾸고 파일별 실패 기록을 함께 반환합니다."""
    entries = []
    failures = []
    total_bytes = 0
    image_count = 0
    archive_entries = 0
    input_dir = job_dir / "uploads"
    input_dir.mkdir(exist_ok=True)

    def count_image():
        """실패 사진도 포함한 전체 사진 수를 세고 작업 한도를 적용합니다."""
        nonlocal image_count
        image_count += 1
        if image_count > settings.max_files:
            raise InputLimitError(f"사진이 최대 {settings.max_files:,}장을 넘었습니다. 나누어 올려 주세요.")

    # 메타데이터로 실제로 꺼낼 사진의 바이트 합계를 계산합니다.
    expanded_total = 0
    for path, _ in uploads:
        if path.suffix.lower() == ".zip":
            try:
                inspect_zip_metadata(path, settings.max_archive_entries)
                with ZipFile(path) as archive:
                    expanded_total += sum(m.file_size for m in archive.infolist()
                                          if Path(m.filename).suffix.lower() in EXTENSIONS)
            except (BadZipFile, OSError):
                pass
        else:
            expanded_total += path.stat().st_size
    if on_progress:
        on_progress("extracting", 0, expanded_total)
    for archive_index, (path, name) in enumerate(uploads):
        if path.suffix.lower() != ".zip":
            count_image()
            total_bytes += path.stat().st_size
            if total_bytes > settings.max_expanded_bytes:
                raise InputLimitError("압축 해제 후 전체 사진 용량 제한을 넘었습니다.")
            entries.append((path, name))
            if on_progress:
                on_progress("extracting", total_bytes, expanded_total)
            continue

        try:
            inspect_zip_metadata(path, settings.max_archive_entries)
            with ZipFile(path) as archive:
                members = archive.infolist()
                archive_entries += len(members)
                if archive_entries > settings.max_archive_entries:
                    raise InputLimitError(f"ZIP 내부 항목 합계가 {settings.max_archive_entries:,}개를 넘었습니다.")
                for index, member in enumerate(members):
                    normalized = member.filename.replace("\\", "/")
                    parts = normalized.split("/")
                    if member.is_dir() or parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
                        continue
                    filename = safe_filename(normalized)
                    report_name = name + " / " + "/".join(
                        safe_filename(part) for part in parts if part and part not in {".", ".."}
                    )[-1000:]
                    mode = member.external_attr >> 16
                    # extract()를 쓰지 않고 검사한 바이트만 서버가 정한 새 경로에 저장합니다.
                    if normalized.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", normalized) or stat.S_ISLNK(mode):
                        failures.append({"source": report_name, "error": "안전하지 않은 ZIP 경로나 심볼릭 링크입니다."})
                        continue
                    if Path(filename).suffix.lower() not in EXTENSIONS:
                        failures.append({"source": report_name, "error": "지원하지 않는 파일입니다. 중첩 압축파일은 풀지 않습니다."})
                        continue
                    count_image()
                    if member.flag_bits & 1:
                        failures.append({"source": report_name, "error": "암호화된 ZIP 항목은 지원하지 않습니다."})
                        continue
                    if member.file_size > settings.max_file_bytes:
                        failures.append({"source": report_name, "error": "압축 해제한 사진이 파일당 용량 제한을 넘었습니다."})
                        continue
                    if total_bytes + member.file_size > settings.max_expanded_bytes:
                        raise InputLimitError("압축 해제 후 전체 사진 용량 제한을 넘었습니다.")
                    target = input_dir / f"extracted_{archive_index:04d}" / f"{index:06d}" / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = 0
                    try:
                        with archive.open(member) as source, target.open("xb") as output:
                            while chunk := source.read(1024 * 1024):
                                extracted += len(chunk)
                                total_bytes += len(chunk)
                                if extracted > settings.max_file_bytes or total_bytes > settings.max_expanded_bytes:
                                    raise InputLimitError("압축 해제 중 실제 용량 제한을 넘었습니다.")
                                output.write(chunk)
                                if on_progress:
                                    on_progress("extracting", total_bytes, expanded_total)
                    except InputLimitError:
                        raise
                    except (BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error, lzma.LZMAError):
                        target.unlink(missing_ok=True)
                        failures.append({"source": report_name, "error": "ZIP 항목이 손상됐거나 지원하지 않는 압축 방식입니다."})
                    else:
                        entries.append((target, report_name))
        except InputLimitError:
            raise
        except (BadZipFile, RuntimeError, NotImplementedError, EOFError):
            failures.append({"source": name, "error": "ZIP을 읽을 수 없습니다. 일반 ZIP 파일인지 확인해 주세요."})
        finally:
            path.unlink(missing_ok=True)
    return entries, failures


def safe_filename(filename: str) -> str:
    """사용자 파일명에서 경로·제어 문자와 Windows 예약 이름을 제거합니다."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip(" .")
    path = Path(name)
    stem = path.stem[:100].strip(" .") or "photo"
    if stem.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
        *(f"LPT{i}" for i in range(10)),
    }:
        stem = "_" + stem
    # CSV를 스프레드시트로 열 때 파일명이 수식으로 실행되지 않게 합니다.
    if stem.startswith(("=", "+", "-", "@")):
        stem = "_" + stem
    return stem + path.suffix[:10].lower()


def validate_image(path: Path, max_pixels: int) -> None:
    """확장자·실제 형식·디코딩·픽셀 수를 확인합니다. 원본 바이트는 수정하지 않습니다."""
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError("지원하지 않는 확장자입니다.")
    # verify만으로 확인되지 않는 잘린 이미지도 load로 끝까지 검사합니다.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format not in FORMATS:
                raise ValueError("실제 이미지 형식은 JPG, PNG, WEBP만 지원합니다.")
            if image.width * image.height > max_pixels:
                raise ValueError(f"사진의 픽셀 수가 제한({max_pixels:,})을 넘었습니다.")
            image.verify()
        with Image.open(path) as image:
            image.load()


def process_job(job_dir: Path, entries: list[tuple[Path, str]], predictor, settings, on_progress=None):
    """업로드 파일을 검증한 뒤 기존 sorter로 분류합니다. 결과 경로·개수를 반환합니다."""
    if on_progress:
        on_progress("extracting", 0, None)
    entries, failures = expand_uploads(job_dir, entries, settings, on_progress)
    if on_progress:
        on_progress("validating", 0, len(entries) + len(failures))
    names = {}
    input_dir = job_dir / "uploads"
    for index, (path, name) in enumerate(entries, start=1):
        try:
            validate_image(path, settings.max_pixels)
        except Exception as error:
            # 손상된 파일은 제외하되 정상 파일의 분류는 계속 진행합니다.
            reason = str(error) if isinstance(error, ValueError) else "이미지를 정상적으로 읽을 수 없습니다."
            failures.append({"source": name, "error": reason})
            path.unlink()
        else:
            names[path.relative_to(input_dir).as_posix()] = name
        if on_progress:
            on_progress("validating", index, len(entries))
    result = sort_to_zip(
        input_dir, job_dir / "results", predictor,
        initial_failures=failures, source_names=names, allow_empty=True,
        on_progress=on_progress,
    )
    # 검토·선택 다운로드를 위해 분류된 원본 사본과 CSV를 만료 시점까지 보관합니다.
    final_zip = job_dir / "sorted_photos.zip"
    result.zip_path.replace(final_zip)
    shutil.rmtree(input_dir)
    result.job_dir.replace(job_dir / "review")
    shutil.rmtree(job_dir / "results")
    return final_zip, result.counts, result.failed_count


class ProcessLock:
    """같은 작업 폴더를 사용하는 서버가 하나만 실행되도록 OS 파일 잠금을 유지합니다."""

    def __init__(self, path: Path):
        """잠금 파일 경로를 받아 수명 동안 열어둘 파일 객체를 준비합니다."""
        self.path = path
        self.stream = None

    def acquire(self):
        """Windows와 Linux에서 비차단 잠금을 얻습니다. 중복 서버이면 시작을 거부합니다."""
        import os
        self.stream = self.path.open("a+b")
        try:
            # 다른 Windows 프로세스가 잠근 바이트에 쓰지 않도록 빈 파일만 초기화합니다.
            self.stream.seek(0, 2)
            if self.stream.tell() == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise RuntimeError("동일 WORK_DIR 서버가 실행 중입니다. --workers 1로 실행하세요.") from None

    def release(self):
        """파일을 닫아 OS 잠금을 해제합니다."""
        if self.stream:
            self.stream.close()
            self.stream = None
