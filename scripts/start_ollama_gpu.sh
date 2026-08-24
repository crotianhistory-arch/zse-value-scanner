#!/usr/bin/env bash
set -euo pipefail

# Optional convenience launcher for ZSE Value Scanner.
# It NEVER installs/updates drivers, CUDA, Ollama, or models.
# The Python scanner can autostart Ollama by itself; this script is useful when
# you want to start/check the service manually.

OLLAMA_PORT="${OLLAMA_PORT:-11434}"
OLLAMA_HOST_ADDR="${OLLAMA_HOST_ADDR:-127.0.0.1}"
OLLAMA_CONTEXT_LENGTH="${ZSE_LLM_CONTEXT:-2048}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "ERROR: ollama is not installed or not on PATH." >&2
  exit 2
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "=== NVIDIA GPU ==="
  nvidia-smi --query-gpu=index,uuid,name,memory.total,memory.free --format=csv,noheader
else
  echo "WARNING: nvidia-smi not found. ZSE scanner defaults to require_gpu=true." >&2
fi

if curl -fsS "http://127.0.0.1:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
  echo "Ollama already running on localhost:${OLLAMA_PORT}."
else
  echo "Starting Ollama on localhost:${OLLAMA_PORT}..."
  export OLLAMA_HOST="${OLLAMA_HOST_ADDR}:${OLLAMA_PORT}"
  export OLLAMA_CONTEXT_LENGTH
  export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"
  nohup ollama serve > "${TMPDIR:-/tmp}/zse_ollama.log" 2>&1 &
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
      echo "Ollama ready."
      break
    fi
    sleep 0.25
  done
fi

echo
echo "Installed models:"
ollama list || true

echo
echo "Loaded models / processor placement:"
ollama ps || true

echo
echo "Next checks:"
echo "  zse-tool llm-status"
echo "  zse-tool llm-test"
