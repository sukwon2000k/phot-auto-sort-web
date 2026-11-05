"""사진 폴더를 3종으로 분류하고, 원본 사본과 분류된 ZIP을 만듭니다.

설치 위치: photo-auto-sorter/app/services/sorter.py
predictor.py를 같은 폴더에 둔 뒤 VS Code 실행 버튼으로 시험할 수 있습니다.
"""

import argparse
import csv
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol
from zipfile import ZIP_DEFLATED, ZipFile


# __file__을 기준으로 하므로 터미널의 현재 폴더가 달라도 동작합니다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "app" / "asset"
DEFAULT_CHECKPOINT = (
    PROJECT_ROOT / "models" / "head_20261103_230028_536214" / "best_head.pth"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs"
CLASSES = ("food", "landscape", "person")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}


class Predictor(Protocol):
    """분류 기능에 필요한 예측기의 약속입니다. 기존 PhotoPredictor와 호환됩니다."""

    def predict_path(self, image_path: str | Path) -> str:
        """이미지 경로를 받아 food, landscape, person 중 하나를 반환합니다."""
        ...


@dataclass
class SortResult:
    """웹이나 실행 코드에서 사용할 결과 경로, 클래스별 개수, 실패 개수입니다."""

    job_dir: Path
    images_dir: Path
    zip_path: Path
    report_path: Path
    counts: dict[str, int]
    failed_count: int


def list_images(input_dir: Path, recursive: bool = True) -> list[Path]:
    """입력 폴더에서 지원 확장자의 이미지 경로를 정렬해서 반환합니다.

    recursive=True이면 하위 폴더도 검색합니다. 바로 가기 형태의 심볼릭 링크
    파일과 지원하지 않는 확장자는 처리 대상에서 제외합니다.
    """
    if not input_dir.is_dir():
        raise NotADirectoryError(f"입력 폴더를 찾지 못했습니다: {input_dir}")
    candidates = input_dir.rglob("*") if recursive else input_dir.iterdir()
    return sorted(
        (
            path for path in candidates
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: path.relative_to(input_dir).as_posix().casefold(),
    )


def unique_name(filename: str, used_names: set[str]) -> str:
    """이름이 겹치면 _2, _3을 붙이고 사용한 이름을 기록합니다.

    Windows에서 ZIP을 풀어도 파일이 겹치지 않도록 대소문자도 무시해 비교합니다.
    """
    original = Path(filename)
    candidate = original.name
    number = 2
    while candidate.casefold() in used_names:
        candidate = f"{original.stem}_{number}{original.suffix}"
        number += 1
    used_names.add(candidate.casefold())
    return candidate


def sort_to_zip(
    input_dir: str | Path,
    output_dir: str | Path,
    predictor: Predictor,
    recursive: bool = True,
    *,
    initial_failures: list[dict[str, str]] | None = None,
    source_names: dict[str, str] | None = None,
    allow_empty: bool = False,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> SortResult:
    """폴더의 이미지를 예측해 복사하고 ZIP을 생성합니다.

    입력:
        input_dir: 분류할 사진 폴더
        output_dir: 실행별 결과 폴더를 생성할 상위 폴더
        predictor: 한 번 로딩한 PhotoPredictor 객체
        recursive: 하위 폴더 포함 여부
        initial_failures: 웹 업로드 검증에서 제외한 파일의 이름과 실패 이유
        source_names: 내부 상대 경로를 보고서용 파일명으로 바꾸는 선택적 매핑
        allow_empty: True이면 성공 사진이 없어도 빈 세 폴더와 실패 보고서를 ZIP으로 반환
        on_progress: 선택적 콜백. 단계·처리 완료 파일 수·전체 파일 수를 전달합니다.
    반환:
        결과 경로와 성공·실패 개수가 담긴 SortResult

    읽지 못한 이미지는 실패로 기록하고 계속 진행합니다. GPU 오류나 복사·ZIP
    저장 오류는 중단하여 완성되지 않은 결과를 성공으로 안내하지 않습니다.
    """
    input_dir = Path(input_dir).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()

    # 결과를 다시 입력으로 읽는 일을 방지합니다.
    if output_dir == input_dir or output_dir.is_relative_to(input_dir):
        raise ValueError("결과 폴더는 입력 폴더 바깥으로 지정하세요.")

    image_paths = list_images(input_dir, recursive=recursive)
    if not image_paths and not allow_empty:
        raise ValueError("지원하는 이미지가 없습니다. JPG, JFIF, PNG 등을 넣어주세요.")

    output_dir.mkdir(parents=True, exist_ok=True)
    # 실행 시간이 겹쳐도 tempfile의 고유 문자열로 결과가 구분됩니다.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir = Path(tempfile.mkdtemp(prefix=f"sorted_{timestamp}_", dir=output_dir))
    images_dir = job_dir / "sorted_images"
    for class_name in CLASSES:
        (images_dir / class_name).mkdir(parents=True)

    counts = dict.fromkeys(CLASSES, 0)
    used_names = {class_name: set() for class_name in CLASSES}
    failed_count = len(initial_failures or [])
    total_count = len(image_paths) + failed_count
    initial_failed_count = failed_count
    if on_progress:
        on_progress("classifying", initial_failed_count, total_count)
    report_path = job_dir / "classification_report.csv"

    # 매 사진의 결과를 즉시 기록하여 처리 경로와 실패 이유를 확인할 수 있게 합니다.
    with report_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["source", "predicted_class", "destination", "status", "error"]
        )
        writer.writeheader()
        # 웹 검증 단계에서 제외한 파일도 같은 보고서에 남깁니다.
        for failure in initial_failures or []:
            writer.writerow({
                "source": failure["source"], "status": "failed",
                "error": failure["error"],
            })

        for index, source in enumerate(image_paths, start=1):
            relative_source = source.relative_to(input_dir).as_posix()
            report_source = (source_names or {}).get(relative_source, relative_source)
            try:
                class_name = predictor.predict_path(source)
            except (OSError, ValueError) as error:
                # 손상되거나 읽을 수 없는 파일은 재시도를 위한 기록을 남깁니다.
                failed_count += 1
                writer.writerow({
                    "source": report_source, "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                })
                stream.flush()
                print(f"[{index}/{len(image_paths)}] 읽기 실패: {relative_source}")
                if on_progress:
                    on_progress("classifying", initial_failed_count + index, total_count)
                continue

            if class_name not in CLASSES:
                raise ValueError(f"예상하지 못한 예측 클래스: {class_name}")

            filename = unique_name(source.name, used_names[class_name])
            destination = images_dir / class_name / filename
            # 사진을 재인코딩하지 않고 원본 파일 그대로 복사합니다.
            # xb는 혹시 대상 파일이 이미 있더라도 덮어쓰지 않는 모드입니다.
            with source.open("rb") as source_stream, destination.open("xb") as dest_stream:
                shutil.copyfileobj(source_stream, dest_stream)
            counts[class_name] += 1
            writer.writerow({
                "source": report_source, "predicted_class": class_name,
                "destination": f"{class_name}/{filename}", "status": "ok", "error": "",
            })
            stream.flush()
            print(f"[{index}/{len(image_paths)}] {relative_source} -> {class_name}/{filename}")
            if on_progress:
                # 예측뿐 아니라 원본 복사와 결과 기록까지 끝난 파일만 셉니다.
                on_progress("classifying", initial_failed_count + index, total_count)

    if sum(counts.values()) == 0 and not allow_empty:
        raise ValueError(f"분류된 이미지가 없습니다. 실패 기록을 확인하세요: {report_path}")

    # ZIP 루트에 세 클래스 폴더와 처리 기록을 담습니다.
    # 실패한 사진은 포함하지 않으며, 기록의 failed 행으로 구분합니다.
    zip_path = job_dir / "sorted_photos.zip"
    if on_progress:
        on_progress("zipping", total_count, total_count)
    with ZipFile(zip_path, mode="x", compression=ZIP_DEFLATED, compresslevel=1) as archive:
        for class_name in CLASSES:
            archive.writestr(f"{class_name}/", b"")
        for path in sorted(images_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(images_dir).as_posix())
        archive.write(report_path, arcname=report_path.name)

    return SortResult(job_dir, images_dir, zip_path, report_path, counts, failed_count)


def main() -> None:
    """기본 경로 또는 터미널 인자로 폴더 분류와 ZIP 생성을 실행합니다."""
    parser = argparse.ArgumentParser(description="사진을 3개 폴더로 분류하고 ZIP을 만듭니다.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--no-recursive", action="store_true", help="입력 폴더 바로 아래만 검색")
    args = parser.parse_args()

    # 실제 모델 로딩은 실행 시에만 하여 웹에서도 함수를 별도로 가져올 수 있습니다.
    if __package__:
        from .predictor import PhotoPredictor
    else:
        from predictor import PhotoPredictor

    print("입력 폴더:", args.input.resolve())
    print("모델 로딩 중...")
    predictor = PhotoPredictor(args.checkpoint, device=args.device)
    print("사용 장치:", predictor.device)

    result = sort_to_zip(args.input, args.output, predictor, recursive=not args.no_recursive)
    print("\n분류 완료")
    for class_name, count in result.counts.items():
        print(f"{class_name}: {count}장")
    print(f"읽기 실패: {result.failed_count}장")
    print("결과 폴더:", result.images_dir)
    print("ZIP 파일:", result.zip_path)
    print("처리 기록:", result.report_path)


if __name__ == "__main__":
    main()

