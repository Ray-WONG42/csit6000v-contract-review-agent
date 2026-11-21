"""Call the course model through the authenticated tcloud SDK."""
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing, contextmanager

from .config import ROOT, settings


JOB_ID = re.compile(r"Submitted batch job (\d+)")
SERVICE_URL = re.compile(r"^SERVICE_BASE_URL=(http://[A-Za-z0-9.-]+:[0-9]{1,5}/v1)[ \r]*$", re.M)
SERVICE_RECHECK_SECONDS = 15


def sdk_executable():
    if os.name == "nt":
        return shutil.which("wsl.exe")
    return shutil.which("tcloud", path=os.path.dirname(sys.executable)) or shutil.which("tcloud")


def _sdk_once(arguments, cwd=None, timeout=180):
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
        executable = sdk_executable() or "tcloud"
        command = [executable, *arguments]
    process = subprocess.run(command, cwd=cwd, capture_output=True, timeout=timeout)
    output = process.stdout.replace(b"\0", b"").decode("utf-8", errors="replace")
    diagnostics = process.stderr.replace(b"\0", b"").decode("utf-8", errors="replace")
    # A returned job ID is authoritative even if the CLI subsequently reports an error.
    if arguments[0] == "submit" and JOB_ID.search(output + "\n" + diagnostics):
        return output + "\n" + diagnostics
    if process.returncode or "[Tcloud Error]" in output or "[Tcloud Error]" in diagnostics:
        # CLI diagnostics can include filenames and account metadata.
        reason = (output + "\n" + diagnostics)[-1800:].strip()
        raise RuntimeError("TACC SDK failed: " + reason)
    if arguments[0] == "submit":
        return output + "\n" + diagnostics
    return output


class TaccSDKError(RuntimeError):
    """Public-safe failure context; raw CLI diagnostics stay out of UI."""
    def __init__(self, command, exc):
        text = str(exc).lower()
        self.code = "command_failed"
        if "invalid job id" in text:
            self.code, cause = "job_missing", "作业已结束或不存在"
        elif "no such file" in text:
            self.code, cause = "file_missing", "远端文件尚不存在"
        elif isinstance(exc, subprocess.TimeoutExpired):
            self.code, cause = "timeout", "连接超时"
        elif any(x in text for x in ("handshake", "failed to dial", "remote session", "connection reset")):
            self.code, cause = "connection", "SSH 连接失败"
        elif "no such file" in text:
            cause = "远端文件尚不存在"
        else:
            cause = "命令执行失败"
        phase = "提交作业" if command == "submit" else "读取作业状态或结果"
        hint = "；提交结果可能不确定，请先核对作业记录，避免重复提交" if command == "submit" else "；请恢复原任务，避免重复创建作业"
        self.public_message = f"TACC SDK {phase}时{cause}{hint}。"
        super().__init__(self.public_message)


def sdk(arguments, cwd=None, timeout=180):
    # Only idempotent reads can be retried after an ambiguous connection failure.
    attempts = 3 if arguments[0] in {"cat", "ps"} else 1
    for attempt in range(attempts):
        try:
            return _sdk_once(arguments, cwd, timeout)
        except (subprocess.TimeoutExpired, RuntimeError) as exc:
            transient = isinstance(exc, subprocess.TimeoutExpired) or any(
                text in str(exc).lower() for text in (
                    "handshake failed", "failed to dial", "failed to create remote session",
                    "connection reset", "connection timed out", "unexpected eof"))
            if not transient or attempt + 1 == attempts:
                raise TaccSDKError(arguments[0], exc) from exc
            time.sleep(2 * (attempt + 1))


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


@contextmanager
def service_lock():
    # Serialize SDK submissions across the chat worker, review worker and CLI.
    # Keep the lock separate from the database that serves incoming requests.
    settings.data.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.data / "tacc-service-lock.db", timeout=300)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        yield


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


class TaccServicePending(Exception):
    def __init__(self, job_id):
        self.job_id = job_id
        super().__init__("等待学校 GPU 集群启动 Qwen")


class TaccServiceError(RuntimeError):
    @property
    def public_message(self):
        return str(self)


def service_job_active(job_id):
    if not str(job_id).isdigit():
        raise ValueError("Invalid service job ID")
    try:
        status = sdk(["ps", "-j", str(job_id)], timeout=15)
    except TaccSDKError as exc:
        if exc.code != "job_missing":
            raise
        return False
    return bool(re.search(r"\b" + str(job_id) + r"\b", status))


def _submit_service(record, cwd=None):
    if record.get("state") == "submitting" and not record.get("job_id"):
        raise TaccServiceError("上次 TACC GPU 作业提交结果不确定，请核对集群作业后恢复。")
    save_service({"state": "submitting", "ready": False})
    try:
        job_id = submit(cwd or ROOT / "tacc" / "serve")
    except FileNotFoundError as exc:
        save_service(record)
        raise TaccServiceError("找不到课程 tcloud SDK，请检查工作台主机配置。") from exc
    now = time.time()
    record = {"job_id": job_id, "ready": False, "checked_at": now, "submitted_at": now}
    save_service(record)
    return record


def start_service_job(cwd=None, job_id=None):
    with service_lock():
        record = service_record()
        job_id = job_id or record.get("job_id")
        if job_id and service_job_active(job_id):
            if job_id != record.get("job_id"):
                save_service({"job_id": job_id, "ready": False})
            return job_id
        return _submit_service(record, cwd)["job_id"]


def inspect_service(job_id):
    try:
        log = service_log(job_id)
    except RuntimeError as exc:
        if getattr(exc, "code", None) != "file_missing" and "No such file" not in str(exc):
            raise
        log = ""
    if any(marker in log for marker in ("CLAUSE_SERVICE_EXIT", "CLAUSE_STARTUP_FAILURE", "Exited with exit code",
                                        "CANCELLED", "TIME LIMIT", "Finished server process", "Application shutdown complete")):
        return {"ready": False, "state": "failed", "base_url": ""}
    base = SERVICE_URL.search(log)
    if base and "Application startup complete" in log:
        return {"ready": True, "state": "ready", "base_url": base[1]}
    return {"ready": False, "state": "waiting", "base_url": ""}


def queued_service_base(checkpoint):
    if settings.tacc_url:
        return get_base_url()
    expected = checkpoint("tacc_service_job") if checkpoint else None
    with service_lock():
        record = service_record()
        job_id = record.get("job_id")
        if expected and expected != job_id:
            raise TaccServiceError("等待中的 TACC GPU 作业已被替换，请重新提交请求。")
        if expected and record.get("state") in {"failed", "stopped"}:
            raise TaccServiceError("TACC GPU 作业已结束或 Qwen 启动失败，请查看集群作业日志。")
        now = time.time()
        if not job_id or now - record.get("checked_at", 0) >= SERVICE_RECHECK_SECONDS:
            if job_id and service_job_active(job_id):
                record.update(inspect_service(job_id), checked_at=time.time())
                save_service(record)
            elif job_id and now - record.get("submitted_at", 0) < 60:
                # A newly submitted job can take a short time to appear in status results.
                record.update(checked_at=time.time())
                save_service(record)
            elif expected:
                record.update(ready=False, state="failed", checked_at=time.time())
                save_service(record)
            else:
                record = _submit_service(record)
        if record.get("ready"):
            return get_base_url()
        if record.get("state") in {"failed", "stopped"}:
            raise TaccServiceError("TACC GPU 作业已结束或 Qwen 启动失败，请查看集群作业日志。")
    if checkpoint and not expected:
        checkpoint("tacc_service_job", record["job_id"])
    raise TaccServicePending(record["job_id"])


def wait_for_result(run, remote_id, event):
    deadline = time.monotonic() + settings.tacc_sdk_timeout
    seen = 0
    poll_delay = 2
    log_path = f"slurm_log/clause-qwen-client-{remote_id}.log"
    while time.monotonic() < deadline:
        try:
            log = sdk(["cat", log_path], timeout=60)
        except RuntimeError as exc:
            if getattr(exc, "code", None) != "file_missing" and "No such file" not in str(exc):
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
        # tcloud invokes a WSL subprocess for each read on Windows. Start with
        # responsive updates, then back off while model inference is running.
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_delay, remaining))
        poll_delay = min(12, poll_delay + 2)
    raise TimeoutError("TACC 客户端等待超时，作业编号 " + remote_id)


def run_remote(operation, event, checkpoint=None, *, auto_start=False, **arguments):
    saved = checkpoint("tacc_client") if checkpoint else None
    run = saved["run"] if saved else uuid.uuid4().hex
    if saved:
        if not saved.get("job_id"):
            raise RuntimeError("上次提交结果不确定，已阻止重复提交；请核对该任务的远端作业后恢复。")
        remote_id = saved["job_id"]
        event("queue", "恢复 TACC 客户端任务", remote_id)
    else:
        base = queued_service_base(checkpoint) if auto_start else get_base_url()
        # Submit an isolated folder containing only the query and review engine.
        root = settings.data / "tacc-staging"
        root.mkdir(parents=True, exist_ok=True)
        staging = root / ("clause-" + run)
        staging.mkdir()
        request = staging / "request.json"
        try:
            for name in ("tuxiv.conf", "remote.py", "job.sh", "resolve_endpoint.py"):
                shutil.copyfile(ROOT / "tacc" / "client" / name, staging / name)
            shutil.copyfile(ROOT / "engine.py", staging / "engine.py")
            shutil.copyfile(ROOT / "prompt_loader.py", staging / "prompt_loader.py")
            shutil.copytree(ROOT / "prompts", staging / "prompts")
            model_config = {"temperature": settings.llm_temperature, "timeout": settings.tacc_timeout,
                            "max_tokens": settings.tacc_max_output_tokens,
                            "context_limit": settings.tacc_context_limit}
            request.write_text(json.dumps({"operation": operation, "run": run, "base_url": base,
                                          "model_config": model_config, **arguments},
                                          ensure_ascii=True), encoding="utf-8")
            event("queue", "提交 TACC 客户端任务", "")
            intent = {"run": run, "operation": operation, "state": "submitting"}
            (staging / "receipt.json").write_text(json.dumps(intent), encoding="utf-8")
            if checkpoint:
                checkpoint("tacc_client", intent)
            remote_id = submit(staging)
            # A private receipt survives diagnostic-script exits as well as worker restarts.
            receipt = {"run": run, "job_id": remote_id, "operation": operation}
            (staging / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
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
