#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "${TACC_WORKDIR:?Submit with tcloud}"
source /mnt/home/public/course-envs/CSIT6000V/project/activate.sh
/usr/bin/python3 -u resolve_endpoint.py request.json
qwen-python -u remote.py
