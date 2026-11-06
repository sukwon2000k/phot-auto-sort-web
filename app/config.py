"""웹 서버 환경변수를 읽고 프로젝트 기준 경로와 제한값을 정합니다."""

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_path(value: str) -> Path:
    """환경변수 경로를 받아 상대 경로이면 프로젝트 루트 아래 절대 경로로 반환합니다."""
    path = Path(value).expanduser()
    return (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def positive_env(name: str, default: int) -> int:
    """양의 정수 설정을 읽습니다. 잘못된 설정은 서버 시작 때 명확하게 거부합니다."""
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name}은 양의 정수여야 합니다.")
    return value


@dataclass
class Settings:
    """모델·임시 경로, 업로드 및 보관 제한값을 담는 서버 설정입니다."""

    model_path: Path = field(default_factory=lambda: project_path(os.getenv(
        "MODEL_PATH", "models/finetune_20261104_163209_792837/best_finetune.pth")))
    work_dir: Path = field(default_factory=lambda: project_path(os.getenv("WORK_DIR", ".work")))
    device: str = field(default_factory=lambda: os.getenv("MODEL_DEVICE", "auto"))
    max_files: int = field(default_factory=lambda: positive_env("MAX_FILES", 50000))
    max_file_bytes: int = field(default_factory=lambda: positive_env("MAX_FILE_MB", 20) * 1024**2)
    max_request_bytes: int = field(default_factory=lambda: positive_env("MAX_REQUEST_MB", 20480) * 1024**2)
    max_archive_bytes: int = field(default_factory=lambda: positive_env("MAX_ARCHIVE_MB", 20480) * 1024**2)
    max_expanded_bytes: int = field(default_factory=lambda: positive_env("MAX_EXPANDED_MB", 81920) * 1024**2)
    max_archive_entries: int = field(default_factory=lambda: positive_env("MAX_ARCHIVE_ENTRIES", 100000))
    retention_seconds: int = field(default_factory=lambda: positive_env("RETENTION_SECONDS", 3600))
    cleanup_interval: int = field(default_factory=lambda: positive_env("CLEANUP_INTERVAL_SECONDS", 60))
    upload_timeout: int = field(default_factory=lambda: positive_env("UPLOAD_TIMEOUT_SECONDS", 1800))
    max_pixels: int = field(default_factory=lambda: positive_env("MAX_IMAGE_PIXELS", 40_000_000))

    chunk_bytes: int = field(default_factory=lambda: positive_env("CHUNK_MB", 50) * 1024**2)
    disk_reserve_bytes: int = field(default_factory=lambda: positive_env("DISK_RESERVE_MB", 1024) * 1024**2)

    def __post_init__(self):
        """테스트나 직접 생성한 설정도 양수인지 확인합니다."""
        for name in ("max_files", "max_file_bytes", "max_request_bytes",
                     "max_archive_bytes", "max_expanded_bytes", "max_archive_entries",
                     "retention_seconds", "cleanup_interval", "upload_timeout", "max_pixels", "chunk_bytes", "disk_reserve_bytes"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name}은 양수여야 합니다.")

        if self.chunk_bytes >= 100_000_000:
            raise ValueError("조각 크기는 100MB보다 작아야 합니다. CHUNK_MB=50을 사용하세요.")
