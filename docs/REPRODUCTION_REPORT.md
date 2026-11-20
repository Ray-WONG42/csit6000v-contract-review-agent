# Reproduction evidence / 复现验收记录

Date: 2026-11-20 (Asia/Shanghai).

## Scope and provenance

The supplied GitHub repository was cloned successfully. Its initial HEAD was
`9b3e2df5b7170bf8fd623fcb8e0dcd9986c3c8be`. The application was overlaid in a
separate local release checkout; the original proposal and CUAD Git LFS attribute
were preserved. No remote push was performed.

A new clone of the prepared **local** release repository was created using
`git clone --no-local`, then fast-forwarded to implementation commit `0665fdc`.
This verifies the proposed publication content, not the unchanged public GitHub HEAD.
The final documentation/package metadata may be in a subsequent local commit.

## Environment and checks

- Windows, Python 3.12.14; fresh `.venv` with system-site-packages disabled.
- Installed every exact version from `requirements.txt`. Pip reused its wheel cache;
  this did not reuse the original venv or its site-packages. A cold network download
  on another platform was not tested.
- `python -m pip check`: no broken requirements.
- `.env` copied from `.env.example`; no real model, search or mailbox secret required.
- `python demo.py`: exit 0, `mode=demo`, successful `save_skill` tool, one saved artifact,
  eight-category synthetic contract review, and `status=PASS`.
- `python scripts/smoke_web.py`: exit 0. Fresh API and Worker on isolated port 8790;
  checked bundled HTML/hashed assets, login, actual `create_document` execution,
  authenticated DOCX download and expected document text. Services stopped and
  temporary data cleaned up. No Node installation or browser was used for this check.
- Regression suite: 31 tests pass, including owned-data access checks, idempotency,
  tool planning with controlled model replies, tool disablement and short-term memory configuration.
- Frontend production build: TypeScript checks and Vite build pass.
- Package scanner: allowlist excludes `.env`, data, weights, caches, nested archives,
  session databases, private logs and local progress screenshots; blank secret fields
  and absence of detected personal Windows absolute paths checked in published text.

The FastAPI test client emits one dependency deprecation warning about `httpx`;
it does not fail the tests. A future test-client dependency migration is separate
from this reproduction package.

## Issues found and addressed

1. Prompts had been embedded in Python. They are now separate template files, loaded
   relative to the module and copied into each SDK client submission.
2. Broad dependency ranges could drift. Both requirement files now contain identical exact pins.
3. Windows temporary SQLite cleanup could fail after a successful demo. The CLI now
   disposes the database engine before deleting temporary data.
4. Windows venv child launchers may leave process descendants when only the parent is
   terminated. The launcher/smoke cleanup stops only the process trees it created.
5. Removing the existing Git LFS attribute would have staged the full CUAD archive.
   Its attribute was retained; CUAD is optional and excluded from the compact standalone package.
6. Tool enablement, history inclusion and browser persistence lacked explicit settings;
   they now have documented switches. Durable job recovery remains private and enabled.
7. Model configuration and prompt changes could otherwise reuse outdated survey cache;
   the model signature now includes these prompt/configuration inputs.

These items and the corresponding user actions appear in the README FAQ.

## External validation still required

- **Real TACC:** own course authorization, queue resources and adequate free GPU memory.
  Earlier submissions encountered memory shortages and scheduling delays. No live
  general-chat/tool-planning or custom-review result is claimed by this packaging run.
  Run the connection probe, `demo.py --live`, and `scripts/tacc_review_smoke.py` after service readiness.
- **Outlook:** complete authorization with a dedicated mailbox and actually verify receiving/replying.
- **Cloud / HTTPS:** deployment configuration is included, not remotely deployed by this run.
- **Quality evaluation:** synthetic demo PASS does not measure legal correctness or CUAD accuracy.
- **Other systems/providers:** no Linux/macOS clean installation or Anthropic/Ollama/provider
  integration result is claimed. The Linux process supervisor has its separate simulated-process check.
- **Licensing:** team agreement and course-code redistribution terms must be confirmed before public publication.

中文：这份记录验证的是“待上传内容的本地干净 clone”，没有把离线规则工具演示当作
真实模型验收。上传后仍需从 GitHub 新 clone 再跑同样命令；TACC、邮箱和云部署各自验收。
