"""Write individual measurements and by-arm statistics from saved trial files."""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean, variance


METRICS = ("debate_turns", "risk_turns", "elapsed_seconds", "llm_calls", "llm_successful_calls",
           "llm_error_callbacks", "fallback_count", "llm_calls_without_returned_usage",
           "tool_calls", "tokens_in", "tokens_out", "scripted_model_invocations")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    trials = []
    for path in sorted(args.run_dir.glob("*/trial.json")):
        trials.append({"trial": path.parent.name, **json.loads(path.read_text(encoding="utf-8"))})
    if not trials:
        parser.error("No trial.json files were found.")

    fields = ["trial", "cohort", "data_sha256", "mode", "status", "debate_rounds_configured", *METRICS, "rating", "error_type"]
    with (args.run_dir / "trials.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trials)

    summary = []
    def group(r):
        return (r.get('cohort','legacy_unverified'),r.get('data_sha256','unverified'),r['mode'],r['debate_rounds_configured'])
    for cohort, data_hash, mode, rounds in sorted({group(r) for r in trials}):
        arm = [r for r in trials if group(r) == (cohort, data_hash, mode, rounds)]
        completed = [r for r in arm if r["status"] == "ok"]
        row = {"cohort":cohort,"data_sha256":data_hash,"mode": mode, "rounds": rounds, "attempted": len(arm), "completed": len(completed),
               "failed":len(arm)-len(completed),
               "ratings": dict(Counter(r["rating"] for r in completed)), "metrics": {}}
        for metric in METRICS:
            values = [r[metric] for r in completed if r.get(metric) is not None]
            if values:
                row["metrics"][metric] = {"n": len(values), "mean": mean(values),
                                          "sample_variance": variance(values) if len(values) > 1 else None}
        summary.append(row)
    (args.run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
