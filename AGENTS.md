# Project implementation notes

Use Python/FastAPI/SQLAlchemy with SQLite and React/TypeScript/Vite. Keep external model and mail calls behind the existing adapters. Inspect current implementation before changing module boundaries.

The app has demo and real TACC modes. Demo mode must remain visibly labeled. Tests inject controlled responses. Never claim a live model or email integration was verified without its actual result.

Legal users can access only their own records. The admin can read original input files and final agent reports across users, but cannot read private jobs, traces, skills or drafts. Enforce this in every backend endpoint.

Use stable source paragraph IDs and verify quotations before publication. Keep extraction failure separate from no evidence. Snapshot Skill and policy versions per job. All automatically assigned policy results must cite the selected policy and supporting evidence.

Use local synthetic fixtures for tests. The worker must preserve job consistency across retries. Mail delivery must reuse saved reports and must never take recipients from model output.

Commands from repository root:
- .venv/Scripts/python.exe -m pytest -q (Windows)
- .venv/Scripts/python.exe scripts/launch.py (Windows)
- python -m pytest -q (Linux)
- npm --prefix web run build
- npm --prefix web run types (API must be running)

Read README.md for configuration and docs/progress.md for verification evidence and unresolved external dependencies. Update the progress record when a milestone materially changes.
