#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -x .venv/bin/python ]]; then
  python_cmd=.venv/bin/python
elif [[ -x .venv/Scripts/python.exe ]]; then
  python_cmd=.venv/Scripts/python.exe
else
  echo 'Create .venv and install requirements.txt first.' >&2
  exit 1
fi
exec "$python_cmd" demo.py "$@"
