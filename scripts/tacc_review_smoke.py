"""Exercise the real TACC service with a synthetic clause outside the built-in eight."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.tacc_sdk import run_remote
from engine import parse


def main():
    text = """SYNTHETIC SERVICE AGREEMENT

1. Invoices
The Customer will pay undisputed invoices within 45 calendar days from receipt.

2. Confidentiality
The Supplier must keep Customer information confidential for three years after termination.
"""
    skill = {"name": "付款专项", "categories": [], "explicit": True, "policy": "evidence-only@1",
             "custom_items": [{"title": "付款期限", "question": "客户收到无争议发票后须在几天内付款？"}]}
    result = run_remote("review", lambda stage, title, detail: print(stage, title, detail, flush=True),
                        paragraphs=parse("synthetic.txt", text.encode()), skill=skill,
                        instruction="保密义务在终止后持续几年？")
    rows = result["rows"]
    assert {r["category"] for r in rows} == {"custom_1", "custom_question"}, result
    assert all(r["status"] == "found" and r["evidence"] for r in rows), result
    assert any("45 calendar days" in e["quote"] for r in rows if r["category"] == "custom_1" for e in r["evidence"])
    assert any("three years" in e["quote"] for r in rows if r["category"] == "custom_question" for e in r["evidence"])
    print(json.dumps({"client_job": result["tacc_client_job"], "model_mode": result["model_mode"],
                      "state": result["state"], "categories": [r["category"] for r in rows],
                      "model_calls": len(result["calls"])}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
