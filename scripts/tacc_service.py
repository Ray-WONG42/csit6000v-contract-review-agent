"""Start, inspect or stop Clause's course Qwen service."""
import argparse
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, settings
from app.tacc_sdk import SERVICE_URL, sdk, service_log, service_record, save_service, submit


def poll_ready(job_id, deadline):
    while time.monotonic() < deadline:
        try:
            log = service_log(job_id)
        except RuntimeError as error:
            if "No such file" not in str(error):
                raise
            log = ""
        base = SERVICE_URL.search(log)
        if "CLAUSE_SERVICE_EXIT" in log:
            print(log[-3300:], file=sys.stderr)
            raise RuntimeError(f"TACC 服务 {job_id} 已退出。")
        if base and "Application startup complete" in log:
            if any(x in log for x in ("TIME LIMIT", "CANCELLED", "Finished server process", "Application shutdown complete")):
                raise RuntimeError(f"TACC 服务 {job_id} 已结束。")
            save_service({"job_id": job_id, "base_url": base[1], "ready": True})
            print(f"TACC Qwen ready. Service job {job_id}.", flush=True)
            return
        if any(marker in log for marker in ("Exited with exit code", "CANCELLED", "TIME LIMIT", "CLAUSE_STARTUP_FAILURE", "CLAUSE_SERVICE_EXIT")):
            print(log[-3300:], file=sys.stderr)
            raise RuntimeError(f"TACC 服务 {job_id} 启动失败，请查看作业日志。")
        time.sleep(8)
    raise TimeoutError(f"TACC 服务 {job_id} 仍在排队或启动，稍后运行 status。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "adopt", "status", "stop"))
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--wait", type=int, default=480)
    parser.add_argument("--exclude-node", help="Avoid a known busy TACC node for this submission.")
    args = parser.parse_args()
    saved = service_record()
    job_id = args.job_id or saved.get("job_id")
    if args.command == "start":
        if job_id:
            try:
                state = sdk(["ps", "-j", job_id], timeout=60)
            except RuntimeError as error:
                if "Invalid job id" not in str(error):
                    raise
            else:
                if job_id in state:
                    return poll_ready(job_id, time.monotonic() + args.wait)
        if args.exclude_node:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", args.exclude_node):
                raise ValueError("Invalid TACC node name.")
            settings.data.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="clause-serve-", dir=settings.data) as directory:
                staging = Path(directory)
                source = ROOT / "tacc" / "serve"
                for name in ("job.sh", "service_supervisor.py"):
                    shutil.copyfile(source / name, staging / name)
                config = (source / "tuxiv.conf").read_text(encoding="utf-8")
                (staging / "tuxiv.conf").write_text(config.rstrip() + "\n    - exclude=" + args.exclude_node + "\n", encoding="utf-8")
                job_id = submit(staging)
        else:
            job_id = submit(ROOT / "tacc" / "serve")
        print("Submitted TACC Qwen service:", job_id, flush=True)
        save_service({"job_id": job_id, "ready": False})
    if not job_id or not str(job_id).isdigit():
        raise ValueError("需要有效的 TACC 服务作业编号")
    if args.command == "stop":
        sdk(["cancel", "-j", job_id], timeout=60)
        save_service({"job_id": job_id, "ready": False})
        print("Stop requested for TACC job", job_id)
        return
    if args.command == "adopt":
        save_service({"job_id": job_id, "ready": False})
    poll_ready(job_id, time.monotonic() + args.wait)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc))
