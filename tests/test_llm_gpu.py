from zse_tool.llm.gpu import GIB, choose_gpu, parse_nvidia_smi_csv, vram_budget_bytes
from zse_tool.llm.ollama import InstalledModel, candidate_models


def test_parse_nvidia_smi_and_choose_most_free_gpu():
    text = "0, GPU-aaa, RTX A, 12288, 9000\n1, GPU-bbb, RTX B, 24576, 7000\n"
    gpus = parse_nvidia_smi_csv(text)
    assert len(gpus) == 2
    assert choose_gpu(gpus).index == 0
    assert choose_gpu(gpus, 1).uuid == "GPU-bbb"


def test_vram_budget_keeps_headroom():
    gpu = parse_nvidia_smi_csv("0, GPU-aaa, RTX, 12288, 10000\n")[0]
    budget = vram_budget_bytes(gpu, reserve_gib=1.0, max_fraction_of_total=0.90)
    assert budget <= int(12 * GIB * 0.90) + 2 * 1024 * 1024
    assert budget <= 9 * GIB


def test_auto_model_candidates_choose_largest_installed_that_fits():
    models = [
        InstalledModel("tiny:latest", 2 * GIB, family="qwen"),
        InstalledModel("medium:latest", 5 * GIB, family="qwen"),
        InstalledModel("large:latest", 9 * GIB, family="qwen"),
        InstalledModel("embeddinggemma:latest", 1 * GIB, family="embeddinggemma"),
    ]
    out = candidate_models(models, budget_bytes=6 * GIB)
    assert [m.name for m in out] == ["medium:latest", "tiny:latest"]


def test_explicit_model_must_be_installed_but_bypasses_auto_size_filter():
    models = [InstalledModel("chosen:latest", 9 * GIB, family="qwen")]
    out = candidate_models(models, budget_bytes=2 * GIB, explicit_model="chosen:latest")
    assert [m.name for m in out] == ["chosen:latest"]
