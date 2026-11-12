"""Set up TradingAgents and run the course experiment from a portable checkout."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfile
import subprocess
import sys
from tempfile import TemporaryDirectory


CODE = Path(__file__).resolve().parent
COURSE = CODE.parent
RUNTIME = COURSE / ".tradingagents-runtime"
RUNS = COURSE / "runs"
ENV_FILE = RUNTIME / ".env"
PYTHON = RUNTIME / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
UPSTREAM = "https://github.com/TauricResearch/TradingAgents.git"
REVISION = "3e44faafa042daf953f6ec2422ae36bb1fd4f50f"
RUNTIME_SCRIPTS = (
    "experiment_support.py", "rerun_preflight.py", "run_experiment.py",
    "summarize_experiment.py", "test_experiment_controls.py",
)
PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
START = "2021-11-10"
CUTOFF = "2026-11-09"
END_EXCLUSIVE = "2026-11-10"


def run(command, *, cwd=RUNTIME):
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", TRADINGAGENTS_NO_UPDATE_CHECK="1")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def setup():
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Run setup with Python 3.12.")
    if not RUNTIME.exists():
        with TemporaryDirectory(prefix=".runtime-", dir=COURSE) as temporary:
            checkout = Path(temporary) / "checkout"
            run(["git", "init", str(checkout)], cwd=COURSE)
            run(["git", "fetch", "--depth", "1", UPSTREAM, REVISION], cwd=checkout)
            run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=checkout)
            checkout.rename(RUNTIME)
    if not (RUNTIME / ".git").is_dir():
        raise ValueError(f"Expected the managed Git checkout at {RUNTIME}.")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=RUNTIME, text=True).strip()
    if head != REVISION:
        raise ValueError(f"Expected TradingAgents revision {REVISION} in {RUNTIME}; found {head}.")
    for name in RUNTIME_SCRIPTS:
        copyfile(CODE / name, RUNTIME / name)
    if not PYTHON.is_file():
        run([sys.executable, "-m", "venv", str(PYTHON.parent.parent)])
    run([str(PYTHON), "-m", "pip", "install", "-c",
         str(COURSE / "requirements-observed.txt"), "-e", str(RUNTIME)])
    if not ENV_FILE.exists():
        copyfile(COURSE / "env.template", ENV_FILE)
    print(f"Runtime ready: {RUNTIME}")
    print(f"DeepSeek configuration file: {ENV_FILE}")


def new_run(label):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return RUNS / f"{label}-{stamp}"


def offline():
    output = new_run("offline")
    run([str(PYTHON), "-B", "-m", "unittest", "-v", "test_experiment_controls"])
    run([str(PYTHON), "-B", str(RUNTIME / "run_experiment.py"), "--offline", "--output", str(output)])
    run([str(PYTHON), "-B", str(RUNTIME / "summarize_experiment.py"), str(output)])
    print(f"Offline results: {output}")


def download_snapshot(directory):
    import yfinance as yf

    directory.mkdir()
    yf.set_tz_cache_location(str(directory / "yfinance-cache"))
    history = yf.Ticker("NVDA").history(
        start=START, end=END_EXCLUSIVE, interval="1d", auto_adjust=True,
        actions=False, timeout=30, raise_errors=True,
    )
    if history.empty or not set(PRICE_COLUMNS).issubset(history.columns):
        raise ValueError("Yahoo Finance did not return the required daily NVDA OHLCV data.")

    dates = history.index.strftime("%Y-%m-%d")
    frame = history[PRICE_COLUMNS].reset_index(drop=True)
    frame.insert(0, "Date", dates)
    frame = frame.loc[frame["Date"].between(START, CUTOFF)].sort_values("Date")
    if (
        frame.empty or frame["Date"].iloc[0] != START or frame["Date"].iloc[-1] != CUTOFF
        or frame["Date"].duplicated().any() or frame[PRICE_COLUMNS].isna().any().any()
    ):
        raise ValueError(f"Invalid NVDA bars for {START} through {CUTOFF}: check the endpoints, dates and prices.")

    target = directory / "NVDA-YFin-data.csv"
    frame.to_csv(target, index=False, encoding="utf-8", lineterminator="\n")
    metadata = {
        "source": "Yahoo Finance via yfinance",
        "ticker": "NVDA",
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "yfinance_version": yf.__version__,
        "interval": "1d",
        "auto_adjust": True,
        "start": START,
        "end": CUTOFF,
        "rows": len(frame),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    (directory / "snapshot_source.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Saved {len(frame)} NVDA rows to {target}", flush=True)
    return target


def live(earlier_runs):
    from dotenv import dotenv_values

    if not ENV_FILE.is_file():
        raise ValueError(f"Create {ENV_FILE} with setup and enter a DeepSeek API key.")
    with ENV_FILE.open(encoding="utf-8-sig") as config:
        if not dotenv_values(stream=config).get("DEEPSEEK_API_KEY"):
            raise ValueError(f"Fill in DEEPSEEK_API_KEY in {ENV_FILE}.")
    for earlier in earlier_runs:
        if not any(earlier.glob("*/trial.json")):
            raise ValueError(f"No trial records found in the earlier run directory: {earlier}")

    campaign = new_run("live")
    campaign.mkdir(parents=True)
    print(f"Campaign directory: {campaign}", flush=True)
    snapshot = download_snapshot(campaign / "input")
    run([str(PYTHON), "-B", str(RUNTIME / "rerun_preflight.py"), "--source", str(RUNTIME),
         "--env-file", str(ENV_FILE), "--output", str(campaign / "preflight")])

    runner = [str(PYTHON), "-B", str(RUNTIME / "run_experiment.py"),
              "--env-file", str(ENV_FILE), "--frozen-csv", str(snapshot)]
    round1, round2, pairs = (campaign / name for name in ("live-round1", "live-round2", "three-pairs"))
    run([*runner, "--debate-rounds", "1", "--output", str(round1)])
    run([*runner, "--debate-rounds", "2", "--output", str(round2)])
    run([*runner, "--pairs", "3", "--output", str(pairs)])

    export = [str(PYTHON), "-B", str(CODE / "export_experiment.py"),
              "--live-round1", str(round1), "--live-round2", str(round2),
              "--pairs", str(pairs), "--output", str(campaign / "export")]
    for earlier in earlier_runs:
        export.extend(["--extra-run", str(earlier)])
    run(export)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Fetch the pinned runtime and install dependencies.")
    commands.add_parser("offline", help="Run the unit tests and scripted workflow checks.")
    live_command = commands.add_parser("live", help="Download prices, run eight live trials and export.")
    live_command.add_argument("--earlier-run", type=Path, action="append", default=[],
                              help="Include an earlier run directory in the inventory. May be repeated.")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
        elif not PYTHON.is_file() or not (RUNTIME / "run_experiment.py").is_file():
            raise ValueError("Run setup before running the experiment.")
        elif args.command == "offline":
            offline()
        else:
            if Path(sys.prefix).resolve() != PYTHON.parent.parent.resolve():
                raise ValueError(f"Run live with the runtime Python at {PYTHON}.")
            live([path.resolve() for path in args.earlier_run])
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"Stopped after a command failed with exit code {error.returncode}. "
                    "See the messages and any campaign directory printed above.\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
