"""Application settings, read from environment variables with sensible defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # Where uploaded files and the SQLite database live.
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("GEO_DATA_DIR", BASE_DIR / "data"))
    )
    # Reject uploads larger than this (compressed size, as received).
    max_upload_mb: int = field(
        default_factory=lambda: int(os.getenv("GEO_MAX_UPLOAD_MB", "50"))
    )
    # Guard against zip bombs: total uncompressed size allowed inside a .zip/.kmz.
    max_uncompressed_mb: int = field(
        default_factory=lambda: int(os.getenv("GEO_MAX_UNCOMPRESSED_MB", "500"))
    )

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def database_url(self) -> str:
        return os.getenv("GEO_DATABASE_URL", f"sqlite:///{self.data_dir / 'app.db'}")
