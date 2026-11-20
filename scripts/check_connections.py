"""Read-only diagnostics. Does not send an email or upload a contract."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings
from mailbox import Outlook
from engine import Model
from prompt_loader import load_prompt
from app.tacc_sdk import run_remote, service_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Perform configured model and mailbox read requests.")
    args = parser.parse_args()
    report = {"model_mode": settings.model_mode, "tacc_url_configured": bool(settings.tacc_url),
              "model_id_configured": bool(settings.tacc_model), "graph_configured": Outlook().enabled,
              "public_url": settings.app_public_url, "secure_cookie": settings.app_secure_cookie,
              "tacc_protocol": settings.tacc_protocol}
    if settings.tacc_protocol == "sdk":
        record = service_record()
        report["tacc_service_job"] = record.get("job_id")
        report["tacc_service_marked_ready"] = bool(record.get("ready") or settings.tacc_url)
    if args.live:
        if settings.tacc_protocol == "sdk":
            try:
                result = run_remote("probe", lambda *_: None)
                report["tacc_test"] = bool(result["ok"])
                report["tacc_client_job"] = result["tacc_client_job"]
                report["model"] = result["model"]
            except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
                report["tacc_test"] = False
                report["tacc_error"] = str(exc)[:250] if not str(exc).startswith("TACC SDK failed:") else "SDK command failed"
        elif settings.tacc_url and settings.tacc_model:
            try:
                model = Model(lambda *_: None, "tacc")
                result = model.call(load_prompt('probe'), {}, "connection_test")
                report["tacc_test"] = result == {"ok": True}
            except Exception as exc:
                report["tacc_test"] = type(exc).__name__
        if Outlook().enabled:
            try:
                Outlook().api("/mailFolders/inbox?$select=id")
                report["graph_read_test"] = True
            except Exception as exc:
                report["graph_read_test"] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
