#!/usr/bin/env bash
set -euo pipefail
umask 077
test "${CONDA_PREFIX:?SDK must load the course bootstrap}" = /mnt/home/public/course-envs/CSIT6000V/assignment1/mpi-gemm-v1
source /mnt/home/public/course-envs/CSIT6000V/project/activate.sh
: "${TACC_WORKDIR:?Submit with tcloud submit}"
: "${QWEN_RUN_DIR:?Course activation must create a run directory}"
if [[ -n "${QWEN_RUN_DIR:-}" ]]; then
    trap 'test -n "$QWEN_RUN_DIR" && rm -rf -- "$QWEN_RUN_DIR"' EXIT
fi
export QWEN_CACHE_HOME="$TACC_USERDIR/CSIT6000V-project-cache"
mkdir -p -m 700 "$QWEN_CACHE_HOME"
port=$((20000 + SLURM_JOB_ID % 20000))
export CLAUSE_SERVICE_PORT="$port"
printf 'SLURM CUDA_VISIBLE_DEVICES=%s\n' "${CUDA_VISIBLE_DEVICES:-unset}"
nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader
if [[ "${CUDA_VISIBLE_DEVICES:-}" =~ ^[0-9]+,[0-9]+$ ]]; then
    while IFS= read -r available; do
        if (( available < 14000 )); then
            echo 'CLAUSE_STARTUP_FAILURE: insufficient free memory on an allocated GPU'
            exit 23
        fi
    done < <(nvidia-smi -i "$CUDA_VISIBLE_DEVICES" --query-gpu=memory.free --format=csv,noheader,nounits)
fi
printf 'SERVICE_BASE_URL=http://%s:%s/v1\n' "$(hostname)" "$port"
cat > "$QWEN_RUN_DIR/clause-serve.sh" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
exec bash "$QWEN_PROJECT_ROOT/runtime.sh" "$QWEN_PROJECT_ROOT/vllm-env/bin/python" \
    -m vllm.entrypoints.openai.api_server \
    --model "$QWEN_MODEL_PATH" --served-model-name qwen3.8-27b \
    --safetensors-load-strategy lazy --host 0.0.0.0 --port "$CLAUSE_SERVICE_PORT" \
    --tensor-parallel-size 2 --language-model-only --dtype bfloat16 \
    --gpu-memory-utilization 0.55 --max-model-len 16384 --max-num-seqs 1 --enforce-eager \
    --max-num-batched-tokens 4096 --reasoning-parser qwen3 --enable-prefix-caching
LAUNCHER
/usr/bin/python3 -u "$TACC_WORKDIR/service_supervisor.py" "$QWEN_RUN_DIR/clause-serve.sh" &
supervisor_pid=$!
forward_stop() {
    kill -"$1" "$supervisor_pid" 2>/dev/null || true
    wait "$supervisor_pid" || true
    exit "$2"
}
trap 'forward_stop TERM 143' TERM
trap 'forward_stop INT 130' INT
wait "$supervisor_pid"
