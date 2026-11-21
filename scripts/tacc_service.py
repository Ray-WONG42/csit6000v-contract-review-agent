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
from app.tacc_sdk import inspect_service, sdk, service_lock, service_record, save_service, start_service_job


def poll_ready(job_id, deadline):
    while time.monotonic() < deadline:
        with service_lock():
            status = inspect_service(job_id)
            record = service_record()
            if record.get("job_id") == job_id and record.get("state") != "stopped":
                save_service({**record, **status, "checked_at": time.time()})
        if status["ready"]:
            print(f"TACC Qwen ready. Service job {job_id}.", flush=True)
            return
        if status["state"] == "failed":
            raise RuntimeError(status.get("error") or f"TACC 服务 {job_id} 启动失败，请查看作业日志。")
        time.sleep(8)
    raise TimeoutError(f"TACC 服务 {job_id} 仍在排队或启动，稍后运行 status。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "adopt", "status", "stop"))
    parser.add_argument("job_id", nargs="?")
    parser.add_argument("--wait", type=int, default=480)
    parser.add_argument("--exclude-node", help="Avoid a known busy TACC node for this submission.")
    args = parser.parse_args()
    job_id = args.job_id or service_record().get("job_id")
    if args.command == "start":
        staging = None
        if args.exclude_node:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,64}(?:,[A-Za-z0-9.-]{1,64})*", args.exclude_node):
                raise ValueError("Invalid TACC node name.")
            settings.data.mkdir(parents=True, exist_ok=True)
            # Keep the submission folder: WSL may retain handles after tcloud exits.
            # Never let cleanup hide the returned job ID or trigger another submission.
            staging = Path(tempfile.mkdtemp(prefix="clause-serve-", dir=settings.data))
            source = ROOT / "tacc" / "serve"
            for name in ("job.sh", "service_supervisor.py"):
                shutil.copyfile(source / name, staging / name)
            config = (source / "tuxiv.conf").read_text(encoding="utf-8")
            (staging / "tuxiv.conf").write_text(config.rstrip() + "\n    - exclude=" + args.exclude_node + "\n", encoding="utf-8")
        job_id = start_service_job(staging, args.job_id)
        print("TACC Qwen service job:", job_id, flush=True)
    if not job_id or not str(job_id).isdigit():
        raise ValueError("需要有效的 TACC 服务作业编号")
    if args.command == "stop":
        with service_lock():
            sdk(["cancel", "-j", job_id], timeout=60)
            if service_record().get("job_id") == job_id:
                save_service({"job_id": job_id, "state": "stopped", "ready": False})
        print("Stop requested for TACC job", job_id)
        return
    if args.command == "adopt":
        with service_lock():
            save_service({"job_id": job_id, "ready": False})
    poll_ready(job_id, time.monotonic() + args.wait)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc))
