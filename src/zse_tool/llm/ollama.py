from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlparse

import requests

from .gpu import NvidiaGpu, choose_gpu, detect_nvidia_gpus, vram_budget_bytes


@dataclass(frozen=True, slots=True)
class InstalledModel:
    name: str
    size_bytes: int
    parameter_size: str | None = None
    quantization: str | None = None
    family: str | None = None


@dataclass(slots=True)
class OllamaRuntimeStatus:
    enabled: bool
    reachable: bool = False
    started_by_scanner: bool = False
    gpu: NvidiaGpu | None = None
    selected_model: str | None = None
    processor: str | None = None
    reason: str | None = None


class OllamaClient:
    def __init__(self, base_url: str, timeout_seconds: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.trust_env = False

    def _get(self, path: str) -> dict:
        r = self.session.get(self.base_url + path, timeout=self.timeout_seconds)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict, timeout_seconds: float | None = None) -> dict:
        r = self.session.post(
            self.base_url + path,
            json=payload,
            timeout=timeout_seconds or self.timeout_seconds,
        )
        r.raise_for_status()
        return r.json()

    def reachable(self) -> bool:
        try:
            self._get("/api/tags")
            return True
        except (requests.RequestException, ValueError):
            return False

    def list_models(self) -> list[InstalledModel]:
        raw = self._get("/api/tags")
        out: list[InstalledModel] = []
        for item in raw.get("models", []):
            details = item.get("details") or {}
            name = item.get("name") or item.get("model")
            if not name:
                continue
            out.append(
                InstalledModel(
                    name=str(name),
                    size_bytes=int(item.get("size") or 0),
                    parameter_size=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                    family=details.get("family"),
                )
            )
        return out

    def warm_model(self, model: str, *, context_length: int, keep_alive: str) -> dict:
        return self._post(
            "/api/generate",
            {
                "model": model,
                "prompt": "Reply only with OK.",
                "stream": False,
                "think": False,
                "keep_alive": keep_alive,
                "options": {
                    "temperature": 0,
                    "num_ctx": context_length,
                    "num_predict": 2,
                },
            },
            timeout_seconds=max(self.timeout_seconds, 120.0),
        )

    def unload_model(self, model: str) -> None:
        try:
            self._post(
                "/api/generate",
                {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
                timeout_seconds=max(self.timeout_seconds, 30.0),
            )
        except requests.RequestException:
            pass

    def generate_structured(
        self,
        *,
        model: str,
        prompt: str,
        system: str,
        schema: dict,
        context_length: int,
        keep_alive: str,
    ) -> dict:
        raw = self._post(
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "system": system,
                "format": schema,
                "stream": False,
                "think": False,
                "keep_alive": keep_alive,
                "options": {
                    "temperature": 0,
                    "num_ctx": context_length,
                    "num_predict": 160,
                },
            },
            timeout_seconds=max(self.timeout_seconds, 120.0),
        )
        response = raw.get("response", "")
        if isinstance(response, dict):
            return response
        return json.loads(response)


def _looks_like_embedding_model(model: InstalledModel) -> bool:
    text = " ".join(filter(None, [model.name, model.family])).casefold()
    return "embed" in text


def candidate_models(
    models: list[InstalledModel],
    *,
    budget_bytes: int,
    allowlist: tuple[str, ...] = (),
    explicit_model: str | None = None,
) -> list[InstalledModel]:
    by_name = {m.name: m for m in models}
    if explicit_model and explicit_model != "auto":
        return [by_name[explicit_model]] if explicit_model in by_name else []

    allowed = set(allowlist)
    candidates = []
    for model in models:
        if allowed and model.name not in allowed:
            continue
        if not allowed and _looks_like_embedding_model(model):
            continue
        if model.size_bytes <= 0 or model.size_bytes > budget_bytes:
            continue
        candidates.append(model)
    return sorted(candidates, key=lambda m: m.size_bytes, reverse=True)


def ollama_processor(model_name: str, timeout_seconds: float = 8.0) -> str | None:
    """Return PROCESSOR text from `ollama ps` for a loaded model."""
    exe = shutil.which("ollama")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "ps"],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    header = lines[0]
    # PROCESSOR is followed by CONTEXT in current Ollama CLI output.  Use header
    # positions where possible so model names containing punctuation remain safe.
    try:
        p0 = header.index("PROCESSOR")
        p1 = header.index("CONTEXT", p0)
    except ValueError:
        p0 = p1 = -1
    for line in lines[1:]:
        first = line.split(None, 1)[0] if line.split(None, 1) else ""
        if first != model_name and not model_name.startswith(first) and not first.startswith(model_name):
            continue
        if p0 >= 0:
            value = line[p0:p1].strip()
            if value:
                return value
        # Fallback for older/newer spacing: search a percentage processor token.
        tokens = line.split()
        for i, token in enumerate(tokens):
            if token.endswith("%") and i + 1 < len(tokens) and tokens[i + 1] in {"GPU", "CPU"}:
                return f"{token} {tokens[i + 1]}"
    return None


class OllamaManager:
    """Optional Ollama lifecycle + GPU/model selection.

    It never installs/upgrades Ollama, drivers, CUDA, or models.  Autostart only
    runs an already-installed `ollama serve` process.
    """

    def __init__(self, settings, data_dir: Path):
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.client = OllamaClient(settings.base_url, settings.timeout_seconds)
        self._process: subprocess.Popen | None = None

    def _start_local_server(self, gpu: NvidiaGpu | None) -> bool:
        parsed = urlparse(self.settings.base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        exe = shutil.which("ollama")
        if not exe:
            return False
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        env = os.environ.copy()
        env["OLLAMA_HOST"] = f"{host}:{port}"
        env["OLLAMA_CONTEXT_LENGTH"] = str(self.settings.context_length)
        if self.settings.flash_attention:
            env["OLLAMA_FLASH_ATTENTION"] = "1"
        if gpu is not None:
            # Ollama docs recommend UUIDs for stable NVIDIA selection.
            env["CUDA_VISIBLE_DEVICES"] = gpu.uuid
        log_dir = self.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "ollama.log"
        log = open(log_path, "ab", buffering=0)
        try:
            self._process = subprocess.Popen(
                [exe, "serve"],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        except OSError:
            log.close()
            return False
        (self.data_dir / "ollama.pid").write_text(str(self._process.pid), encoding="utf-8")
        deadline = time.monotonic() + self.settings.start_timeout_seconds
        while time.monotonic() < deadline:
            if self.client.reachable():
                return True
            if self._process.poll() is not None:
                return False
            time.sleep(0.4)
        return self.client.reachable()

    def inspect(self) -> OllamaRuntimeStatus:
        status = OllamaRuntimeStatus(enabled=self.settings.enabled)
        gpus = detect_nvidia_gpus()
        status.gpu = choose_gpu(gpus, self.settings.gpu_index)
        status.reachable = self.client.reachable()
        if not self.settings.enabled:
            status.reason = "LLM disabled by configuration"
        elif self.settings.require_gpu and status.gpu is None:
            status.reason = "No usable NVIDIA GPU detected"
        elif not status.reachable:
            status.reason = "Ollama API is not reachable"
        return status

    def prepare(self) -> OllamaRuntimeStatus:
        status = OllamaRuntimeStatus(enabled=self.settings.enabled)
        if not self.settings.enabled:
            status.reason = "LLM disabled by configuration"
            return status

        gpus = detect_nvidia_gpus()
        gpu = choose_gpu(gpus, self.settings.gpu_index)
        status.gpu = gpu
        if self.settings.require_gpu and gpu is None:
            status.reason = "No usable NVIDIA GPU detected; deterministic parser will continue"
            return status

        if not self.client.reachable():
            if not self.settings.autostart:
                status.reason = "Ollama is not running and autostart is disabled"
                return status
            if not self._start_local_server(gpu):
                status.reason = "Could not start/reach local Ollama; deterministic parser will continue"
                return status
            status.started_by_scanner = True
        status.reachable = True

        try:
            installed = self.client.list_models()
        except (requests.RequestException, ValueError) as exc:
            status.reason = f"Could not list installed Ollama models: {exc}"
            return status
        if not installed:
            status.reason = "No local Ollama models installed; scanner never auto-pulls models"
            return status

        if gpu is None:
            # CPU mode is allowed only when require_gpu=False.
            candidates = candidate_models(
                installed,
                budget_bytes=max((m.size_bytes for m in installed), default=0),
                allowlist=self.settings.model_allowlist,
                explicit_model=self.settings.model,
            )
        else:
            budget = vram_budget_bytes(
                gpu,
                reserve_gib=self.settings.vram_reserve_gib,
                max_fraction_of_total=self.settings.max_vram_fraction,
            )
            candidates = candidate_models(
                installed,
                budget_bytes=budget,
                allowlist=self.settings.model_allowlist,
                explicit_model=self.settings.model,
            )

        if not candidates:
            status.reason = "No installed model fits the configured GPU/VRAM budget"
            return status

        for candidate in candidates:
            try:
                self.client.warm_model(
                    candidate.name,
                    context_length=self.settings.context_length,
                    keep_alive=self.settings.keep_alive,
                )
            except (requests.RequestException, ValueError):
                self.client.unload_model(candidate.name)
                continue

            processor = ollama_processor(candidate.name)
            if not self.settings.require_gpu:
                status.selected_model = candidate.name
                status.processor = processor
                return status
            if processor and "100% GPU" in processor.upper():
                status.selected_model = candidate.name
                status.processor = processor
                return status
            self.client.unload_model(candidate.name)

        status.reason = "No candidate model passed the required 100% GPU check"
        return status
