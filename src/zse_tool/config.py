from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on", "y"}


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _env_tuple(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(slots=True)
class LlmSettings:
    enabled: bool = False
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    autostart: bool = True
    require_gpu: bool = True
    model: str = "auto"
    model_allowlist: tuple[str, ...] = ()
    gpu_index: int | None = None
    vram_reserve_gib: float = 1.25
    max_vram_fraction: float = 0.90
    context_length: int = 2048
    keep_alive: str = "5m"
    flash_attention: bool = True
    timeout_seconds: float = 15.0
    start_timeout_seconds: float = 15.0
    mapping_min_confidence: float = 0.95

    @classmethod
    def from_env(cls) -> "LlmSettings":
        return cls(
            enabled=_env_bool("ZSE_USE_LLM", False),
            provider=os.getenv("ZSE_LLM_PROVIDER", "ollama"),
            base_url=os.getenv("ZSE_OLLAMA_URL", "http://127.0.0.1:11434"),
            autostart=_env_bool("ZSE_OLLAMA_AUTOSTART", True),
            require_gpu=_env_bool("ZSE_LLM_REQUIRE_GPU", True),
            model=os.getenv("ZSE_OLLAMA_MODEL", "auto"),
            model_allowlist=_env_tuple("ZSE_OLLAMA_MODELS"),
            gpu_index=_env_optional_int("ZSE_OLLAMA_GPU"),
            vram_reserve_gib=float(os.getenv("ZSE_LLM_VRAM_RESERVE_GIB", "1.25")),
            max_vram_fraction=float(os.getenv("ZSE_LLM_MAX_VRAM_FRACTION", "0.90")),
            context_length=int(os.getenv("ZSE_LLM_CONTEXT", "2048")),
            keep_alive=os.getenv("ZSE_LLM_KEEP_ALIVE", "5m"),
            flash_attention=_env_bool("ZSE_OLLAMA_FLASH_ATTENTION", True),
            timeout_seconds=float(os.getenv("ZSE_OLLAMA_TIMEOUT", "15")),
            start_timeout_seconds=float(os.getenv("ZSE_OLLAMA_START_TIMEOUT", "15")),
            mapping_min_confidence=float(os.getenv("ZSE_LLM_MAPPING_CONFIDENCE", "0.95")),
        )


@dataclass(slots=True)
class Settings:
    data_dir: Path
    db_path: Path
    warehouse_dir: Path | None = None
    timeout_seconds: float = 25.0
    min_request_interval_seconds: float = 1.0
    user_agent: str = "zse-value-scanner/0.1 (private research; respectful rate limit)"
    llm: LlmSettings = field(default_factory=LlmSettings)

    @classmethod
    def from_env(cls, data_dir: str | Path | None = None) -> "Settings":
        root = Path(data_dir or os.getenv("ZSE_DATA_DIR", "data")).expanduser().resolve()
        db = Path(os.getenv("ZSE_DB_PATH", root / "zse.sqlite")).expanduser().resolve()
        warehouse = Path(os.getenv("ZSE_WAREHOUSE_DIR", root / "warehouse")).expanduser().resolve()
        return cls(
            data_dir=root,
            db_path=db,
            warehouse_dir=warehouse,
            timeout_seconds=float(os.getenv("ZSE_TIMEOUT", "25")),
            min_request_interval_seconds=float(os.getenv("ZSE_MIN_REQUEST_INTERVAL", "1.0")),
            user_agent=os.getenv(
                "ZSE_USER_AGENT",
                "zse-value-scanner/0.1 (private research; respectful rate limit)",
            ),
            llm=LlmSettings.from_env(),
        )

    @property
    def resolved_warehouse_dir(self) -> Path:
        return (self.warehouse_dir or (self.data_dir / "warehouse")).expanduser().resolve()

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "files").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
