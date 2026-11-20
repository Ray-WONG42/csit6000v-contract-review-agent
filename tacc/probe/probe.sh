#!/usr/bin/env bash
set -euo pipefail
runtime=/mnt/home/public/course-envs/CSIT6000V/project
echo CLAUSE_PROBE_START
ls -1 "$runtime"
if [[ -f "$runtime/activate.sh" ]]; then
    source "$runtime/activate.sh"
    for name in QWEN_MODEL_DIR QWEN_MODEL_PATH QWEN_CHECKPOINT MODEL_PATH MODEL_DIR QWEN_RUN_DIR; do
        if [[ -n "${!name:-}" ]]; then printf '%s=%s\n' "$name" "${!name}"; fi
    done
    command -v qwen-python
    qwen-python -c 'import importlib.util; print({x: importlib.util.find_spec(x) is not None for x in ("vllm", "transformers", "torch")})'
fi
find /mnt/home/public -maxdepth 6 \( -iname '*27b*' -o -iname 'serve*.sh' -o -iname '*qwen*readme*' \) -print | head -n 35
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
echo CLAUSE_PROBE_END
