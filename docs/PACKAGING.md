# Submission manifest / 提交清单

`[COMMIT]` is public project logic or synthetic data. `[PRIVATE]` must not be
committed. `[GENERATED]` must be initialized after cloning.

```text
.
├── README.md                    [COMMIT] English + Chinese reproduction guide
├── LICENSE / NOTICE             [COMMIT] MIT for own code; course-source notice
├── .gitignore / .gitattributes   [COMMIT] privacy rules and LF shell scripts
├── .env.example                 [COMMIT] blank credentials and public defaults
├── requirements.txt             [COMMIT] exact dependency versions
├── requirements-lock.txt        [COMMIT] identical complete dependency lock
├── demo.py / run_agent.sh        [COMMIT] isolated CLI acceptance workflow
├── prompt_loader.py             [COMMIT] paths resolved from module location
├── prompts/*.txt                [COMMIT] system, tool, reflection, survey, extraction
├── engine.py / mailbox.py       [COMMIT] parsing/retrieval/model/mail adapters
├── app/                         [COMMIT] API, planner, tools, accounts, worker, database schema
├── scripts/                     [COMMIT] launch, fixtures, TACC, diagnostics, package checks
├── migrations/ / alembic.ini     [COMMIT] schema migrations
├── tacc/                        [COMMIT] course launcher, supervisor, CPU client, public config
├── web/src/                     [COMMIT] React UI + browser memory implementation
├── web/package*.json            [COMMIT] frontend dependency lock
├── web/dist/                    [COMMIT] sanitized production assets; avoids Node requirement
├── fixtures/                    [COMMIT] synthetic contracts only
├── tests/ / evals/               [COMMIT] tests and evaluation logic; no private CUAD copy
├── deploy/ / Dockerfile         [COMMIT] optional deployment examples
├── docs/                        [COMMIT] public architecture/demo/TACC/reproduction guides
├── .env / deploy/.env           [PRIVATE] model/mail/admin keys
├── data/                        [GENERATED, PRIVATE] accounts, files, jobs, audit traces, Skill data
├── .venv/ / web/node_modules/    [GENERATED] reinstall; never commit
├── chroma_db/ / faiss/           [GENERATED, PRIVATE] unused by current BM25 implementation
├── models/ / weights/ / .cache/  [GENERATED, PRIVATE] no weights in Git
├── memory/ / sessions/          [GENERATED, PRIVATE] no chat persistence in Git
├── *.db / *.sqlite* / logs/      [GENERATED, PRIVATE] private runtime artifacts
└── release/ / reproduction/     [GENERATED] package and clean-clone verification workspaces
```

Browser messages are stored in IndexedDB/localStorage on the user's machine,
not repository files. They must not be exported into the repository. Backend
job/checkpoint records can contain request text and remain private under `data/`.

Run `python scripts/package_release.py` to create an allowlisted ZIP. The script
also rejects secret-looking values, private key blocks and personal absolute
Windows paths in text files. This is a release check, not a guarantee that all
possible secrets have been detected. Read `git diff --cached` before pushing.

源码、合成样例和必要的前端构建产物提交；密钥、真实合同、结果、运行数据库、
聊天记忆、缓存与日志不提交。打包器按白名单复制，并检查常见敏感内容。
预构建网页中只有 `VITE_*` 公共配置；永远不要把密钥放进 `VITE_*`。

## Operator checklist / 给维护者的操作清单

1. Supply your own TACC SDK credentials privately; never copy another user's account.
2. Fill `.env`; keep it untracked. For real Outlook, privately complete Graph authorization.
3. Preserve LICENSE and NOTICE. The maintainer confirmed public distribution and selected MIT for project-authored code; retain upstream course terms for adapted portions.
4. Rebuild the frontend after any UI or VITE configuration change: `npm --prefix web ci`, then `npm --prefix web run build`.
5. Run `python demo.py`, `python -m pytest -q`, and the package scanner.
6. Inspect `git status --short`, `git diff`, and `git diff --cached`; stage source/config templates/docs only.
7. Commit locally, then push to your repository after review. No force-push is needed.
8. Clone the pushed branch into a new directory, create a fresh venv, install pins, and repeat demo acceptance.
9. For course acceptance additionally run a real TACC probe and `python demo.py --live`; offline demo PASS does not count as real inference.

禁止 push：`.env`、私钥、TACC 凭据、真实合同及报告、会话库、日志、运行快照、模型权重。
若这些曾进入 Git 历史，增加 `.gitignore` 不能清除它们；先撤销相关凭据，再单独处理历史。
