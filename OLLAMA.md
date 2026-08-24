# Optional Ollama / GPU fallback (v0.1.3)

The ZSE scanner does **not** require an LLM. The deterministic XLSX/ADP parser remains the primary path.
Ollama is used only as an optional fallback for unknown spreadsheet sheet names.

## Safety / lifecycle rules

The scanner will never:

- install or update NVIDIA drivers,
- install or update CUDA,
- install or update Ollama,
- pull/download a model automatically,
- silently accept CPU inference when `require_gpu=true`.

When LLM mode is enabled it may start an already-installed local `ollama serve` process.

## Basic commands

Deterministic mode (default):

```bash
zse-tool parse --ticker KOEI
```

Enable Ollama for this invocation:

```bash
zse-tool --use-llm parse --ticker KOEI
```

Inspect GPU + Ollama without starting/loading anything:

```bash
zse-tool llm-status
```

Start Ollama if needed, select a model, verify GPU placement, and run one mapping test:

```bash
zse-tool llm-test
```

You can also start Ollama manually:

```bash
./scripts/start_ollama_gpu.sh
```

## Model selection

Default `ZSE_OLLAMA_MODEL=auto` works only with **already-installed** local models.

1. Read NVIDIA total/free VRAM with `nvidia-smi`.
2. Pick the NVIDIA GPU with most free VRAM (or `ZSE_OLLAMA_GPU=<index>`).
3. Leave configurable VRAM headroom.
4. Sort installed non-embedding models from largest to smallest.
5. Warm candidates in that order.
6. Accept the first candidate whose `ollama ps` reports `100% GPU`.
7. If no model passes, disable LLM fallback and continue deterministically.

The pre-load model-size calculation is deliberately conservative and is only a heuristic. The post-load `100% GPU` check is authoritative for this version.

## Environment variables

See `.env.example`. Important settings:

```bash
export ZSE_USE_LLM=1
export ZSE_OLLAMA_AUTOSTART=1
export ZSE_LLM_REQUIRE_GPU=1
export ZSE_OLLAMA_MODEL=auto
export ZSE_LLM_VRAM_RESERVE_GIB=1.25
export ZSE_LLM_MAX_VRAM_FRACTION=0.90
export ZSE_LLM_CONTEXT=2048
```

Optional model allowlist:

```bash
export ZSE_OLLAMA_MODELS='model-a:tag,model-b:tag,model-c:tag'
```

If set, auto-selection considers only those installed models.

## Learned mappings and review queue

Accepted high-confidence sheet-name mappings are cached in:

```text
data/learned_sheet_aliases.json
```

Low-confidence or failed mappings go to:

```text
data/review_queue.jsonl
```

That means an unusual term normally needs the LLM once; later parses use the deterministic cached alias.

## Cluster use

On a cluster without Ollama/GPU simply leave LLM disabled (the default), or force it:

```bash
zse-tool --no-llm pipeline --ticker KOEI ...
```

No LLM imports or server calls are needed for successful deterministic parsing.
