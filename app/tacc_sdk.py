"""Call the course model through the authenticated tcloud SDK."""
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid

from .config import ROOT, settings


JOB_ID = re.compile(r"Submitted batch job (\d+)")
SERVICE_URL = re.compile(r"^SERVICE_BASE_URL=(http://[A-Za-z0-9.-]+:[0-9]{1,5}/v1)[ \r]*$", re.M)


def sdk(arguments, cwd=None, timeout=180):
    if os.name == "nt":
        if cwd:
            drive = cwd.drive.rstrip(":").lower()
            if not drive or not cwd.is_absolute():
                raise ValueError("SDK 暂存目录必须位于 Windows 本机磁盘。")
            linux_path = "/mnt/" + drive + "/" + "/".join(cwd.parts[1:])
        script = "exec " + shlex.join(["tcloud", *arguments])
        if cwd:
            script = "cd " + shlex.quote(linux_path) + " && " + script
        command = ["wsl.exe", "--", "bash", "-lic", script]
    else:
        command = ["tcloud", *arguments]
    process = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
    output = process.stdout.replace(b"\0", b"").decode("utf-8", errors="replace")
    diagnostics = process.stderr.replace(b"\0", b"").decode("utf-8", errors="replace")
    if process.returncode or "[Tcloud Error]" in output or "[Tcloud Error]" in diagnostics:
        # CLI diagnostics can include filenames and account metadata.
        reason = (output + "\n" + diagnostics)[-1800:].strip()
        raise RuntimeError("TACC SDK failed: " + reason)
    if arguments[0] == "submit":
        return output + "\n" + diagnostics
    return output


def service_file():
    return settings.data / "tacc-service.json"


def service_record():
    path = service_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_service(record):
    path = service_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def service_log(job_id):
    if not str(job_id).isdigit():
        raise ValueError("Invalid service job ID")
    return sdk(["cat", f"slurm_log/clause-qwen-serve-{job_id}.log"], timeout=60)


def get_base_url():
    if settings.tacc_url:
        base = settings.tacc_url.rstrip("/").removesuffix("/chat/completions")
        if not SERVICE_URL.fullmatch("SERVICE_BASE_URL=" + base):
            raise ValueError("SDK 模式需要课程日志中提供的 http://节点:端口/v1 地址。")
        return base
    record = service_record()
    if record.get("base_url") and record.get("ready"):
        if not SERVICE_URL.fullmatch("SERVICE_BASE_URL=" + str(record["base_url"])):
            raise ValueError("保存的 TACC 服务地址格式不正确，请重新启动模型服务。")
        return record["base_url"]
    raise ValueError("请先运行 Start-TACC.cmd，等待课程 Qwen 服务就绪。")


def submit(cwd):
    output = sdk(["submit"], cwd=cwd, timeout=240)
    match = JOB_ID.search(output)
    if not match:
        raise RuntimeError("TACC SDK 未返回任务编号")
    return match[1]


def wait_for_result(run, remote_id, event):
    deadline = time.monotonic() + settings.tacc_sdk_timeout
    seen = 0
    log_path = f"slurm_log/clause-qwen-client-{remote_id}.log"
    while time.monotonic() < deadline:
        try:
            log = sdk(["cat", log_path], timeout=60)
        except RuntimeError as exc:
            if "No such file" not in str(exc):
                raise
            log = ""
        lines = [line.removeprefix("CLAUSE_PROGRESS ") for line in log.splitlines() if line.startswith("CLAUSE_PROGRESS ")]
        for line in lines[seen:]:
            stage, title = json.loads(line)
            event(stage, title, "")
        seen = len(lines)
        if f"CLAUSE_RESULT_READY {run}" in log:
            envelope = json.loads(sdk(["cat", f"clause-results/{run}.json"], timeout=60))
            if "error" in envelope:
                raise RuntimeError("TACC 模型执行失败 (" + envelope["error"] + ")")
            return envelope
        if "Traceback (most recent call last)" in log or "Exited with exit code" in log or "CANCELLED" in log:
            raise RuntimeError("TACC 客户端执行失败，请查看作业日志 " + remote_id)
        time.sleep(6)
    raise TimeoutError("TACC 客户端等待超时，作业编号 " + remote_id)


def run_remote(operation, event, checkpoint=None, **arguments):
    saved = checkpoint("tacc_client") if checkpoint else None
    run = saved["run"] if saved else uuid.uuid4().hex
    if saved:
        remote_id = saved["job_id"]
        event("queue", "恢复 TACC 客户端任务", remote_id)
    else:
        base = get_base_url()
        # Submit an isolated folder containing only the query and review engine.
        root = settings.data / "tacc-staging"
        root.mkdir(parents=True, exist_ok=True)
        staging = root / ("clause-" + run)
        staging.mkdir()
        request = staging / "request.json"
        try:
            for name in ("tuxiv.conf", "remote.py", "job.sh"):
                shutil.copyfile(ROOT / "tacc" / "client" / name, staging / name)
            shutil.copyfile(ROOT / "engine.py", staging / "engine.py")
            shutil.copyfile(ROOT / "prompt_loader.py", staging / "prompt_loader.py")
            shutil.copytree(ROOT / "prompts", staging / "prompts")
            model_config = {"temperature": settings.llm_temperature,
                            "max_tokens": settings.tacc_max_output_tokens,
                            "context_limit": settings.tacc_context_limit}
            request.write_text(json.dumps({"operation": operation, "run": run, "base_url": base,
                                          "model_config": model_config, **arguments},
                                          ensure_ascii=True), encoding="utf-8")
            event("queue", "提交 TACC 客户端任务", "")
            remote_id = submit(staging)
            if checkpoint:
                checkpoint("tacc_client", {"run": run, "job_id": remote_id})
            event("queue", "TACC 客户端已提交", remote_id)
        finally:
            request.unlink(missing_ok=True)
    envelope = wait_for_result(run, remote_id, event)
    for stage, title, detail in envelope.get("events", []):
        if detail and stage in {"validate", "review", "scope", "retrieve", "expand"}:
            event("detail", title, detail)
    return {**envelope["result"], "model_mode": "tacc", "tacc_client_job": remote_id}
