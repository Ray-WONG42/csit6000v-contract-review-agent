"""Run the review inside TACC's network from an SDK CPU job."""
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace, ModuleType
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlsplit


def main():
    source = Path("request.json")
    request = json.loads(source.read_text(encoding="utf-8"))
    source.unlink()
    run = request["run"]
    if not re.fullmatch("[a-f0-9]{32}", run):
        raise ValueError("Invalid run identifier")
    output_dir = Path(os.environ["TACC_USERDIR"]) / "clause-results"
    output_dir.mkdir(mode=0o700, exist_ok=True)
    output = output_dir / (run + ".json")
    url = urlsplit(request["base_url"])
    if url.scheme != "http" or not url.port or url.path != "/v1" or url.query or url.fragment or url.username:
        raise ValueError("Expected the course service base URL")
    # The shared review engine needs only model settings here. Database, accounts,
    # and original files stay on the application server.
    module = ModuleType("app.config")
    config = request.get("model_config", {})
    module.settings = SimpleNamespace(
        tacc_url=request["base_url"] + "/chat/completions",
        tacc_model="qwen3.8-27b",
        tacc_timeout=75,
        tacc_json_mode=False,
        tacc_context_limit=min(16384, max(4096, int(config.get("context_limit", 16384)))),
        tacc_max_output_tokens=min(4096, max(128, int(config.get("max_tokens", 1800)))),
        llm_temperature=max(0, min(2, float(config.get("temperature", 0)))),
        tacc_protocol="chat",
        tacc_poll_url="",
        tacc_api_key="",
        tacc_disable_thinking=True,
    )
    sys.modules["app.config"] = module
    from engine import Model, review
    traces = []
    def event(stage, title, detail=""):
        traces.append([stage, title, detail])
        print("CLAUSE_PROGRESS", json.dumps([stage, title], ensure_ascii=True), flush=True)
    model = Model(event, "tacc")
    start = time.monotonic()
    try:
        deadline = time.monotonic() + 360
        while True:
            try:
                with urlopen(request["base_url"] + "/models", timeout=6) as response:
                    data = json.load(response)
                if any(item.get("id") == module.settings.tacc_model for item in data.get("data", [])):
                    break
                raise ValueError("Unexpected model in TACC response")
            except (OSError, URLError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Course model is not ready")
                time.sleep(8)
        if request["operation"] == "decision":
            result = model.call(request["system"], request["payload"], "assistant")
        elif request["operation"] == "probe":
            value = model.call('Return exactly the JSON object {"ok":true} without any surrounding explanation.', {}, "probe")
            if value != {"ok": True}:
                raise ValueError("Model did not return the requested probe JSON")
            result = {"ok": True, "model": module.settings.tacc_model, "calls": model.calls}
        else:
            result = review(request["paragraphs"], request["skill"], request["instruction"], model,
                            method=request.get("method", "retrieval"), evaluation=request.get("evaluation", False))
        envelope = {"result": result, "events": traces, "elapsed": round(time.monotonic() - start, 3)}
    except Exception as exc:
        envelope = {"error": type(exc).__name__, "calls": model.calls}
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(envelope, ensure_ascii=True), encoding="utf-8")
    os.replace(str(temporary), str(output))
    print("CLAUSE_RESULT_READY", run, flush=True)


if __name__ == "__main__":
    main()
