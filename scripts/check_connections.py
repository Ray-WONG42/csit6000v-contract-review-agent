"""Read-only diagnostics. Does not send an email or upload a contract."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings
from app.services import graph_client
from engine import Model
from prompt_loader import load_prompt
from app.tacc_sdk import run_remote, service_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Perform configured model and mailbox read requests.")
    args = parser.parse_args()
    report = {"model_mode": settings.model_mode, "tacc_url_configured": bool(settings.tacc_url or settings.llm_base_url),
              "model_id_configured": bool(settings.tacc_model or settings.model_name), "mail_configured": graph_client().enabled, "mail_provider": settings.mail_provider,
              "public_url": settings.app_public_url, "secure_cookie": settings.app_secure_cookie,
              "tacc_protocol": settings.tacc_protocol}
    if settings.model_mode == "ollama":
        report["ollama_model"] = settings.ollama_model
        report["ollama_url_configured"] = bool(settings.ollama_url)
    if settings.tacc_protocol == "sdk":
        record = service_record()
        report["tacc_service_job"] = record.get("job_id")
        report["tacc_service_marked_ready"] = bool(record.get("ready") or settings.tacc_url)
    if args.live:
        if settings.model_mode == "ollama":
            try:
                result = Model(lambda *_: None, "ollama").call(load_prompt("probe"), {}, "connection_test")
                report["ollama_test"] = result == {"ok": True}
            except (ValueError, RuntimeError, OSError) as exc:
                report["ollama_test"] = False
                report["ollama_error"] = str(exc)[:250]
        elif settings.model_mode == "api":
            try:
                result = Model(lambda *_: None, "api").call(load_prompt("probe"), {}, "connection_test")
                report["api_test"] = result == {"ok": True}
            except Exception as exc:
                report["api_test"] = type(exc).__name__
        elif settings.tacc_protocol == "sdk":
            try:
                result = run_remote("probe", lambda *_: None)
                report["tacc_test"] = bool(result["ok"])
                report["tacc_client_job"] = result["tacc_client_job"]
                report["model"] = result["model"]
            except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
                report["tacc_test"] = False
                report["tacc_error"] = str(exc)[:250] if not str(exc).startswith("TACC SDK failed:") else "SDK command failed"
        elif (settings.tacc_url or settings.llm_base_url) and (settings.tacc_model or settings.model_name):
            try:
                model = Model(lambda *_: None, "tacc")
                result = model.call(load_prompt('probe'), {}, "connection_test")
                report["tacc_test"] = result == {"ok": True}
            except Exception as exc:
                report["tacc_test"] = type(exc).__name__
        if graph_client().enabled:
            try:
                mailbox = graph_client()
                if settings.mail_provider == "gmail" and settings.gmail_transport == "imap":
                    with mailbox.connect():
                        pass
                else:
                    mailbox.api("/profile" if settings.mail_provider == "gmail" else "/mailFolders/inbox?$select=id")
                report["mail_read_test"] = True
            except Exception as exc:
                report["mail_read_test"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
