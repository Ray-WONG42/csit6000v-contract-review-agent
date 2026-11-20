"""Paired extraction evaluation. External CUAD manifests use the same gold format."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import re
import statistics
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import parse, review, Model, DEFAULT_SKILL, RULES
from app.config import settings
from app.tacc_sdk import run_remote


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def f1(prediction, gold):
    predicted, expected = Counter(tokens(prediction)), Counter(tokens(gold))
    overlap = sum((predicted & expected).values())
    if not overlap:
        return 0.0
    precision = overlap / sum(predicted.values())
    recall = overlap / sum(expected.values())
    return 2 * precision * recall / (precision + recall)


def score(result, gold):
    rows = {row["category"]: row for row in result["rows"]}
    scored = []
    for category, spans in gold.items():
        row = rows.get(category, {"status": "review", "evidence": []})
        quotes = [e["quote"] for e in row["evidence"]]
        values = [f1(quote, span) for quote in quotes for span in spans]
        value = max(values, default=0)
        positive = bool(spans)
        scored.append({"category": category, "positive": positive, "f1": value,
                       "hit": positive and value >= .5, "false_positive": not positive and row["status"] == "found",
                       "unresolved": row["status"] == "review"})
    return scored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="fixtures/eval-manifest.json")
    parser.add_argument("--mode", choices=["demo", "tacc"], default="demo")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", default="data/evaluation-results.json")
    args = parser.parse_args()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if args.repeats < 1:
        raise SystemExit("--repeats must be positive")
    randomizer = random.Random(42)
    records = []
    for repeat in range(args.repeats):
        for case in manifest["contracts"]:
            path = (manifest_path.parent / case["file"]).resolve()
            paragraphs = parse(path.name, path.read_bytes())
            methods = ["full", "bm25", "retrieval"]
            randomizer.shuffle(methods)
            for method in methods:
                start = time.perf_counter()
                model = Model(lambda *_: None, args.mode)
                try:
                    skill = {**DEFAULT_SKILL, "explicit": True}
                    if args.mode == "tacc" and settings.tacc_protocol == "sdk":
                        result = run_remote("review", lambda *_: None, paragraphs=paragraphs, skill=skill,
                                            instruction="", method=method, evaluation=True)
                        model.calls = result["calls"]
                    else:
                        result = review(paragraphs, skill, "", model, method, evaluation=True)
                    records.append({"contract": case["id"], "repeat": repeat, "method": method,
                                    "seconds": time.perf_counter() - start, "calls": model.calls,
                                    "scores": score(result, case["gold"]), "result": result, "error": None})
                except Exception as exc:
                    records.append({"contract": case["id"], "repeat": repeat, "method": method,
                                    "seconds": time.perf_counter() - start, "calls": model.calls, "scores": [],
                                    "error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
    # Primary table uses only contract/repeat pairs where all three methods completed.
    complete_pairs = {(case["id"], repeat) for case in manifest["contracts"] for repeat in range(args.repeats)
                      if all(not r["error"] for r in records if r["contract"] == case["id"] and r["repeat"] == repeat)}
    summary = {}
    for method in ("full", "bm25", "retrieval"):
        selected = [r for r in records if r["method"] == method and (r["contract"], r["repeat"]) in complete_pairs]
        scores = [s for record in selected for s in record["scores"]]
        positives = [s for s in scores if s["positive"]]
        negatives = [s for s in scores if not s["positive"]]
        calls = [c for record in selected for c in record["calls"]]
        latencies = sorted(r["seconds"] for r in selected)
        summary[method] = {
            "paired_runs": len(selected), "positive_pairs": len(positives), "negative_pairs": len(negatives),
            "hit_rate": sum(s["hit"] for s in positives) / len(positives) if positives else None,
            "mean_positive_span_f1": statistics.mean(s["f1"] for s in positives) if positives else None,
            "false_positive_rate": sum(s["false_positive"] for s in negatives) / len(negatives) if negatives else None,
            "unresolved_pairs": sum(s["unresolved"] for s in scores),
            "median_seconds": statistics.median(latencies) if latencies else None,
            "p95_seconds": latencies[min(len(latencies)-1, int((len(latencies)-1)*.95))] if latencies else None,
            "input_tokens": sum(c["input_tokens"] for c in calls) if calls and all(c["input_tokens"] is not None for c in calls) else None,
            "output_tokens": sum(c["output_tokens"] for c in calls) if calls and all(c["output_tokens"] is not None for c in calls) else None,
            "call_count": len(calls),
            "per_category": {category: [s for s in scores if s["category"] == category] for category in RULES},
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"mode": args.mode, "synthetic": manifest.get("synthetic", False),
                                 "note": "demo results validate the harness only; they are not model performance results",
                                 "summary": summary, "runs": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": args.mode, "output": str(output), "paired_runs_per_method": len(complete_pairs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
