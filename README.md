# TradingAgents Debate Reproduction

This CUHK FTEC5660 project tests whether TradingAgents follows its configured researcher debate length and completes the workflow through the final rating. The target is the debate described in Section 3.2 and Figure 3 of [TradingAgents, arXiv:2412.20138v7](https://arxiv.org/abs/2412.20138v7).

| Setting | Baseline | Treatment |
| --- | --- | --- |
| Research debate | 1 round, 2 turns | 2 rounds, 4 turns |
| Risk discussion | 1 round, 3 turns | 1 round, 3 turns |

Both groups analyzed NVDA as of 9 November 2026 with the Market Analyst, empty initial memory, and the same frozen market data source. Both model tiers used `deepseek-flash` through the DeepSeek API. The local Python process ran the workflow and stored evidence. The experiment measured workflow behavior and resource use on this task.

## Install on Windows

Use Git and Python 3.12. The recorded environment was Windows 11 with Python 3.12.0. The fork is based on TradingAgents v0.7.1 (upstream commit `c454491`). Run the commands from the repository root in PowerShell. Calling Python directly avoids the need to activate a PowerShell script.

```powershell
git clone https://github.com/milala-lala/TradingAgents.git
Set-Location TradingAgents
$repo = (Get-Location).Path
py -3.12 -m venv .venv
$python = Join-Path $repo '.venv\Scripts\python.exe'
& $python -m pip install -c .\reproducibility\requirements-observed.txt -e .
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $python -B -c "import sys, tradingagents; print(sys.version.split()[0]); print(tradingagents.__version__); print(tradingagents.__file__)"
```

The import check should show version `0.7.1` and a `tradingagents\__init__.py` path inside the checkout. [Observed dependency versions](requirements-observed.txt) pin the listed packages. Other dependencies are resolved from the project metadata.

## Check the workflow offline

These checks use no API credentials. The offline workflow uses synthetic market data and a scripted model. Its expected research and risk turn counts are `2 / 3` and `4 / 3`.

```powershell
& $python -B -m unittest -v test_experiment_controls
if ($LASTEXITCODE -ne 0) { throw 'The control tests failed.' }
$offline = Join-Path $repo ('runs\offline-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\run_experiment.py --offline --output $offline
if ($LASTEXITCODE -ne 0) { throw "The offline check failed. See $offline." }
& $python -B .\summarize_experiment.py $offline
```

## Configure DeepSeek and the market snapshot

Live runs require a DeepSeek API key and network access. Copy the [blank template](env.template) to `.env` and fill in `DEEPSEEK_API_KEY` in the local editor. Keep the filled file outside Git. The commands below preserve an existing `.env`.

```powershell
$envFile = Join-Path $repo '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath .\reproducibility\env.template -Destination $envFile
}
notepad.exe $envFile
```

The original pilot saved `NVDA-YFin-data.csv` from Yahoo Finance through `yfinance`. It contains 1,255 daily OHLCV records from 10 November 2021 to 10 November 2026. The experiment loader excludes the last day and supplies 1,254 records through the analysis cutoff. The [yfinance project](https://github.com/ranaroussi/yfinance) directs users to Yahoo's terms for data usage rights. Use a local copy of the exact snapshot obtained through an authorized channel. The recorded hash identifies the original file. A fresh download can produce a different snapshot.

```powershell
$snapshot = Read-Host 'Enter the full path to NVDA-YFin-data.csv without quotes'
if (-not (Test-Path -LiteralPath $snapshot -PathType Leaf)) {
    throw 'The NVDA snapshot file was not found.'
}
$expectedHash = '08895ba226978118e59a475e75bcaa303f9812460eee1b664945edefce0cbf49'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $snapshot).Hash -ne $expectedHash) {
    throw 'The CSV differs from the recorded input snapshot.'
}

$probe = Join-Path $repo ('runs\preflight-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\rerun_preflight.py --source $repo --env-file $envFile --output $probe
if ($LASTEXITCODE -ne 0) { throw "The DeepSeek preflight failed. See $probe\api_probe.json or stop.json." }
```

The preflight makes one short model request. A successful probe verifies basic connectivity at the time of the check. Longer calls may still fail. Proceed with the live commands after a successful probe.

## Run one live trial

Choose one of the following blocks. Each block makes paid DeepSeek calls and writes to a new output directory. The one-round and two-round commands differ in the debate setting and the output path.

**One-round baseline**

```powershell
$run = Join-Path $repo ('runs\debate-rounds1-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\run_experiment.py --env-file $envFile --frozen-csv $snapshot --debate-rounds 1 --output $run
```

**Two-round treatment**

```powershell
$run = Join-Path $repo ('runs\debate-rounds2-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\run_experiment.py --env-file $envFile --frozen-csv $snapshot --debate-rounds 2 --output $run
```

Inspect `$run\pilot\trial.json` for the status, turn counts, usage, and rating. The complete reports are under `$run\pilot\report\`. On failure, check `run.log` and `llm_events.jsonl` in the pilot directory before retrying.

## Run and summarize three pairs

This command starts a separate six-trial campaign in the order 1/2, 2/1, 1/2. The runner stops at the first failed trial and retains its record. The summarizer counts failed trials and calculates means and sample variances from completed trials.

```powershell
$batch = Join-Path $repo ('runs\formal-pairs3-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\run_experiment.py --env-file $envFile --frozen-csv $snapshot --pairs 3 --output $batch
$runExit = $LASTEXITCODE
if (Get-ChildItem -Path "$batch\*\trial.json" -File -ErrorAction SilentlyContinue) {
    & $python -B .\summarize_experiment.py $batch
}
Write-Host "Runner exit code $runExit"
Write-Host "Batch directory $batch"
```

The new batch contains `trials.csv` and `summary.json` once the summarizer runs. Each trial directory contains its effective configuration and evidence. The baseline and treatment use `max_debate_rounds` of 1 and 2, respectively, in [run_experiment.py](../run_experiment.py). The underlying turn routing is in [conditional_logic.py](../tradingagents/graph/conditional_logic.py).

## Inspect the recorded results

The saved results can be reviewed without a key or market snapshot.

| Artifact | What it shows |
| --- | --- |
| [Trials](results/trials.csv) and [summary](results/summary.csv) | Per-attempt measurements and grouped statistics. Use `formal_frozen_one_round` and `formal_frozen_two_round` for the formal comparison. |
| [Attempt inventory](results/attempt_inventory.csv) | Recorded successes, failures, and interruptions. |
| [Data checks](results/formal_data_checks.csv) | Checks of the frozen inputs and cutoff across the completed trials. |
| [Completed campaign](evidence/formal-pairs3-20261111-162701-363/) | Six trial records, effective settings, data-read traces, and reports. |
| [Earlier formal attempt](evidence/formal-pairs3-20261111-162435-688/) | The failed one-round trial before the completed campaign. |
| [Original pilot](evidence/original-pilot/) and [frozen two-round pilot](evidence/debate-rounds2-20261111-161434-813/pilot/) | The two feasibility runs discussed in the report. Their metrics are separate from the formal comparison. |

The `record` column in the consolidated trials table preserves the original local run paths. The evidence links above point to the files selected for review.

The six completed trials followed the expected turn counts. The two-round trials took 49.7 seconds longer on average, and the final rating differed in one of the three pairs. The comparison covers one stock and one date. Repeating the same workflow can yield different model-generated arguments and ratings.

## Keep local files out of Git

Stage the course scripts, documentation, and reviewed evidence by path. Keep `.env`, `.venv/`, and newly generated `runs/` local. Use the blank template from this project. Add `/runs/` to the root `.gitignore` if it is missing. Review `git diff --cached --stat` and `git diff --cached` before each commit, especially changes to configurations and logs.
