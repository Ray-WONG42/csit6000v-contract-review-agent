#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "${TACC_WORKDIR:?Submit with tcloud}"
source /mnt/home/public/course-envs/CSIT6000V/project/activate.sh
qwen-python -u remote.py
