"""Export four CSV tables and evidence from saved live TradingAgents runs."""

import argparse
import csv
import json
from pathlib import Path
from shutil import copyfile
from statistics import mean, variance
from tempfile import TemporaryDirectory


METRICS = (
    "debate_turns", "risk_turns", "elapsed_seconds", "llm_calls",
    "llm_successful_calls", "llm_error_callbacks", "fallback_count",
    "llm_calls_without_returned_usage", "tool_calls", "tokens_in", "tokens_out",
)
TRIAL_FIELDS = (
    "group", "batch", "trial", "pair", "mode", "ticker", "date", "status",
    "debate_rounds_configured", "risk_rounds_configured", *METRICS,
    "usage_complete", "rating", "data_sha256", "error_type", "http_status", "record",
)
SUMMARY_FIELDS = (
    "group", "attempted", "success", "failed", "metric", "n", "mean", "sample_variance",
)
INVENTORY_FIELDS = (
    "batch", "trial", "group", "status", "debate_rounds_configured",
    "error_type", "http_status", "record",
)
DATA_FIELDS = (
    "trial", "actual_price_reads", "last_date", "cutoff", "frozen_frame_sha256",
    "source_sha256", "all_dates_within_cutoff", "all_reads_match_frozen_frame",
)
TRIAL_EVIDENCE = ("data_manifest.json", "data_reads.jsonl", "effective_config.json")
REPORT_FILES = ("report/complete_report.html", "report/complete_report.md")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_trial(source, group, evidence_dir, pair=None):
    return {
        **read_json(source),
        "group": group,
        "batch": source.parent.parent.name,
        "trial": source.parent.name,
        "pair": pair,
        "record": (evidence_dir / "trial.json").as_posix(),
    }


def summarize(rows):
    for group in dict.fromkeys(row["group"] for row in rows):
        arm = [row for row in rows if row["group"] == group]
        successes = [row for row in arm if row["status"] == "ok"]
        for metric in METRICS:
            values = [row[metric] for row in successes if row.get(metric) is not None]
            yield {
                "group": group,
                "attempted": len(arm),
                "success": len(successes),
                "failed": len(arm) - len(successes),
                "metric": metric,
                "n": len(values),
                "mean": mean(values) if values else None,
                "sample_variance": variance(values) if len(values) > 1 else "N/A",
            }


def check_data(source, trial):
    manifest = read_json(source.parent / "data_manifest.json")
    trace = (source.parent / "data_reads.jsonl").read_text(encoding="utf-8")
    reads = [json.loads(line) for line in trace.splitlines() if line.strip()]
    dates = [read["latest_returned_date"] for read in reads if read.get("latest_returned_date")]
    cutoff = trial["date"]
    frozen_hash = trial["data_sha256"]
    source_hash = manifest["source_sha256"]
    return {
        "trial": trial["trial"],
        "actual_price_reads": len(reads),
        "last_date": max(dates, default=None),
        "cutoff": cutoff,
        "frozen_frame_sha256": frozen_hash,
        "source_sha256": source_hash,
        "all_dates_within_cutoff": (
            bool(dates) and manifest["cutoff"] == cutoff
            and all(day <= cutoff for day in dates)
            and all(not read["returned_rows"] or read.get("latest_returned_date") for read in reads)
        ),
        "all_reads_match_frozen_frame": (
            bool(reads) and bool(frozen_hash) and manifest["consumed_frame_sha256"] == frozen_hash
            and all(
                read["consumed_frame_sha256"] == frozen_hash and read["source_sha256"] == source_hash
                for read in reads
            )
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-round1", type=Path, required=True, help="Completed one-round run directory.")
    parser.add_argument("--live-round2", type=Path, required=True, help="Completed two-round run directory.")
    parser.add_argument("--pairs", type=Path, required=True, help="Completed three-pair batch directory.")
    parser.add_argument("--extra-run", type=Path, action="append", default=[],
                        help="Earlier attempt directory for the inventory. May be repeated.")
    parser.add_argument("--output", type=Path, required=True, help="New output directory.")
    args = parser.parse_args()

    inputs = [
        (args.live_round1 / "pilot/trial.json", "live_round1", "live-round1", 1, None),
        (args.live_round2 / "pilot/trial.json", "live_round2", "live-round2", 2, None),
    ]
    formal_groups = {1: "formal_frozen_one_round", 2: "formal_frozen_two_round"}
    for pair in range(1, 4):
        for rounds in (1, 2):
            name = f"pair-{pair}-rounds-{rounds}"
            inputs.append((args.pairs / name / "trial.json", formal_groups[rounds], "three-pairs", rounds, pair))
    expected_pairs = {source.resolve() for source, _, _, _, pair in inputs if pair is not None}
    if {path.resolve() for path in args.pairs.glob("*/trial.json")} != expected_pairs:
        parser.error("--pairs must point to a batch with all six trial records.")
    if args.output.exists():
        parser.error(f"Choose a new output directory. This one already exists: {args.output}")

    rows = []
    data_checks = []
    copies = []
    seen = set()
    try:
        for source, group, folder, rounds, pair in inputs:
            source = source.resolve()
            relative = Path("evidence", folder, source.parent.name)
            row = load_trial(source, group, relative, pair)
            if row["mode"] != "live" or row["status"] != "ok" or row["debate_rounds_configured"] != rounds:
                raise ValueError(f"Expected a successful live trial configured for {rounds} round(s): {source}")
            if pair is not None:
                data_checks.append(check_data(source, row))
            rows.append(row)
            copies.append((source.parent, relative, ("trial.json", *TRIAL_EVIDENCE, *REPORT_FILES)))
            seen.add(source)

        inventory = rows.copy()
        for earlier in args.extra_run:
            records = sorted(earlier.glob("*/trial.json"))
            if not records:
                raise ValueError(f"No trial.json files found in {earlier}")
            for source in records:
                source = source.resolve()
                if source in seen:
                    continue
                seen.add(source)
                relative = Path("evidence", "earlier-attempts", source.parent.parent.name, source.parent.name)
                inventory.append(load_trial(source, "earlier_attempt", relative))
                copies.append((source.parent, relative, ("trial.json",)))

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".experiment-export-", dir=args.output.parent) as temporary:
            staging = Path(temporary) / "bundle"
            tables = staging / "results"
            tables.mkdir(parents=True)
            write_csv(tables / "trials.csv", TRIAL_FIELDS, rows)
            write_csv(tables / "summary.csv", SUMMARY_FIELDS, summarize(rows))
            write_csv(tables / "attempt_inventory.csv", INVENTORY_FIELDS, inventory)
            write_csv(tables / "formal_data_checks.csv", DATA_FIELDS, data_checks)
            for source_dir, relative, files in copies:
                for name in files:
                    destination = staging / relative / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    copyfile(source_dir / name, destination)
            staging.rename(args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Export failed: {error}\n")

    print(f"Exported {len(rows)} live trials, {len(inventory)} inventory entries and four CSV tables.")
    print(f"Output directory: {args.output.resolve()}")


if __name__ == "__main__":
    main()
