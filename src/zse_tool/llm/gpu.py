from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess

GIB = 1024 ** 3
MIB = 1024 ** 2


@dataclass(frozen=True, slots=True)
class NvidiaGpu:
    index: int
    uuid: str
    name: str
    total_vram_bytes: int
    free_vram_bytes: int

    @property
    def total_vram_gib(self) -> float:
        return self.total_vram_bytes / GIB

    @property
    def free_vram_gib(self) -> float:
        return self.free_vram_bytes / GIB


def parse_nvidia_smi_csv(text: str) -> list[NvidiaGpu]:
    gpus: list[NvidiaGpu] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",", 4)]
        if len(parts) != 5:
            continue
        idx, uuid, name, total_mib, free_mib = parts
        try:
            gpus.append(
                NvidiaGpu(
                    index=int(idx),
                    uuid=uuid,
                    name=name,
                    total_vram_bytes=int(float(total_mib)) * MIB,
                    free_vram_bytes=int(float(free_mib)) * MIB,
                )
            )
        except ValueError:
            continue
    return gpus


def detect_nvidia_gpus(timeout_seconds: float = 5.0) -> list[NvidiaGpu]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [
                exe,
                "--query-gpu=index,uuid,name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_nvidia_smi_csv(proc.stdout)


def choose_gpu(gpus: list[NvidiaGpu], preferred_index: int | None = None) -> NvidiaGpu | None:
    if not gpus:
        return None
    if preferred_index is not None:
        for gpu in gpus:
            if gpu.index == preferred_index:
                return gpu
        return None
    return max(gpus, key=lambda g: g.free_vram_bytes)


def vram_budget_bytes(
    gpu: NvidiaGpu,
    *,
    reserve_gib: float = 1.25,
    max_fraction_of_total: float = 0.90,
) -> int:
    """Conservative model-weight budget before an actual Ollama load test.

    The model's on-disk size is only a heuristic for VRAM consumption.  This
    function deliberately leaves headroom for context/KV cache and runtime
    allocations.  The selected model must still pass the post-load GPU check.
    """
    reserve = int(max(0.0, reserve_gib) * GIB)
    free_budget = max(0, gpu.free_vram_bytes - reserve)
    total_budget = int(gpu.total_vram_bytes * max(0.1, min(max_fraction_of_total, 1.0)))
    return min(free_budget, total_budget)
