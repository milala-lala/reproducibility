# TradingAgents Debate Reproduction

This CUHK FTEC5660 project tests how TradingAgents responds to a change in the research debate limit. A Market Analyst examines NVDA, Bull and Bear Researchers debate the evidence, and a Research Manager and Trader propose an action. Three risk analysts review the proposal before the Portfolio Manager issues a rating.

| Setting | One-round baseline | Two-round treatment |
| --- | --- | --- |
| Research debate | 1 round, 2 turns | 2 rounds, 4 turns |
| Risk discussion | 1 round, 3 turns | 1 round, 3 turns |

The six formal trials analyzed NVDA as of 9 November 2026. They used the same frozen market data, empty initial memory, and DeepSeek `deepseek-flash` for both model tiers. The experiment checks workflow structure and measures run time and token usage for this task. The study draws on Section 3.2 and Figure 3 of [TradingAgents, arXiv:2412.20138v7](https://arxiv.org/abs/2412.20138v7).

## Install on Windows

Use Git and Python 3.12. In PowerShell, run `python --version` to check that `python` points to a Python 3.12 interpreter. If the command is unavailable or resolves to another version, use the full path to a Python 3.12 executable for the virtual environment command below.

Open a PowerShell window in a parent folder that does not already contain `reproducibility` or `TradingAgents-runtime`. This submission contains the course scripts, recorded data, and results. The separate TradingAgents v0.7.1 checkout supplies the base project. The course scripts run from the base project's root.

```powershell
git clone https://github.com/milala-lala/reproducibility.git
if ($LASTEXITCODE -ne 0) { throw 'Could not clone the course repository.' }

git clone --depth 1 --branch v0.7.1 https://github.com/TauricResearch/TradingAgents.git TradingAgents-runtime
if ($LASTEXITCODE -ne 0) { throw 'Could not clone TradingAgents v0.7.1.' }

$course = (Resolve-Path -LiteralPath '.\reproducibility').Path
$repo = (Resolve-Path -LiteralPath '.\TradingAgents-runtime').Path
$scripts = Get-ChildItem -LiteralPath (Join-Path $course 'code') -File -Filter '*.py'
Copy-Item -LiteralPath $scripts.FullName -Destination $repo -ErrorAction Stop
Set-Location -LiteralPath $repo

python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
$python = Join-Path $repo '.venv\Scripts\python.exe'
$constraints = Join-Path $course 'requirements-observed.txt'
& $python -m pip install -c $constraints -e .
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

& $python -B -c "import sys, tradingagents; print(sys.version.split()[0]); print(tradingagents.__version__); print(tradingagents.__file__)"
```

The import check should show Python 3.12, TradingAgents `0.7.1`, and a `tradingagents\__init__.py` path under `TradingAgents-runtime`. The recorded environment used Windows 11 and Python 3.12.0. The [constraints file](requirements-observed.txt) lists selected observed versions. Pip resolves the remaining packages from the base project metadata.

## Check the workflow offline

Continue in the same PowerShell window. The tests and offline workflow use no API key. The workflow uses synthetic market data and a scripted model. Check that `status` is `ok` in both trials, with research and risk turn counts of 2 and 3 in the first trial and 4 and 3 in the second.

```powershell
& $python -B -m unittest -v test_experiment_controls
if ($LASTEXITCODE -ne 0) { throw 'The control tests failed.' }

$offline = Join-Path $repo ('runs\offline-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\run_experiment.py --offline --output $offline
if ($LASTEXITCODE -ne 0) { throw "The offline check failed. See $offline." }
& $python -B .\summarize_experiment.py $offline
Write-Host "Offline results in $offline"
```

## Configure the live runs

Live runs require a DeepSeek API key and network access. Copy the blank template to the runtime checkout, then fill in `DEEPSEEK_API_KEY` in the editor and save the file. Keep the filled `.env` local. The commands preserve an existing file. [Blank key template](env.template)

```powershell
$envFile = Join-Path $repo '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $course 'env.template') -Destination $envFile
}
notepad.exe $envFile
```

The original pilot saved 1,255 daily OHLCV records from Yahoo Finance through `yfinance`. The frozen loader excludes the 10 November 2026 record and serves 1,254 rows through 9 November 2026. The SHA-256 check below verifies that the raw file matches the recorded snapshot. [Original CSV](evidence/original-pilot/cache/NVDA-YFin-data.csv)

After saving `.env`, check the snapshot and run a short API probe. A successful probe confirms connectivity for that request. The full experiment also needs a connection that can sustain longer responses.

```powershell
$snapshot = Join-Path $course 'evidence\original-pilot\cache\NVDA-YFin-data.csv'
if (-not (Test-Path -LiteralPath $snapshot -PathType Leaf)) {
    throw 'The original NVDA snapshot is missing.'
}
$expectedHash = '08895ba226978118e59a475e75bcaa303f9812460eee1b664945edefce0cbf49'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $snapshot).Hash -ne $expectedHash) {
    throw 'The CSV differs from the recorded input snapshot.'
}

$probe = Join-Path $repo ('runs\preflight-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\rerun_preflight.py --source $repo --env-file $envFile --output $probe
if ($LASTEXITCODE -ne 0) { throw "The API preflight failed. Check the files in $probe." }
```

## Run one live trial

Use either block after the preflight succeeds. Run both blocks if you plan to export both standalone trials. Each run makes paid DeepSeek calls and gets a new output directory. The commands change only the research debate limit and output path. The risk limit stays at one round. [Experiment runner](code/run_experiment.py) · [TradingAgents turn routing](https://github.com/TauricResearch/TradingAgents/blob/v0.7.1/tradingagents/graph/conditional_logic.py)

**One-round baseline**

```powershell
$liveRound1 = Join-Path $repo ('runs\debate-rounds1-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\run_experiment.py --env-file $envFile --frozen-csv $snapshot --debate-rounds 1 --output $liveRound1
```

**Two-round treatment**

```powershell
$liveRound2 = Join-Path $repo ('runs\debate-rounds2-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
& $python -B .\run_experiment.py --env-file $envFile --frozen-csv $snapshot --debate-rounds 2 --output $liveRound2
```

In either output directory, open `pilot/trial.json` for the run status, counts, usage, and rating. A successful run writes complete reports under `pilot/report/`. If a run fails, keep the trial directory and check `run.log` and `llm_events.jsonl` before retrying.

## Run and summarize three pairs

This starts up to six paid trials in the order 1/2, 2/1, 1/2. The runner stops at the first failure and saves the attempt. When any trial records exist, the summarizer writes `trials.csv` and `summary.json`. It counts failures and calculates means and sample variances from successful trials.

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

## Export the four tables and complete reports

Use this block after the two standalone live runs and the three-pair batch finish successfully. It reads the saved records and copies the original HTML and Markdown reports. It runs locally and needs no DeepSeek key. [Export script](code/export_experiment.py)

```powershell
& {
    if (-not ($liveRound1 -and $liveRound2 -and $batch)) {
        throw 'Set $liveRound1, $liveRound2, and $batch to the three completed run folders.'
    }
    $export = Join-Path $repo ('runs\export-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    $exportScript = Join-Path $course 'code\export_experiment.py'
    $extraArgs = @()
    foreach ($earlierRun in $earlierRuns) {
        $extraArgs += @('--extra-run', $earlierRun)
    }
    & $python -B $exportScript --live-round1 $liveRound1 --live-round2 $liveRound2 --pairs $batch --output $export @extraArgs
    if ($LASTEXITCODE -ne 0) { throw 'The export failed. Check the message above.' }
}
```

For runs saved in an earlier session, set `$liveRound1` and `$liveRound2` to the two directories that contain `pilot/`, and `$batch` to the directory that contains the six paired trial folders. Use the `$course`, `$repo`, and `$python` values from the setup section. To record earlier attempts, set `$earlierRuns` to an array of their run directories before exporting. The inventory links to their saved trial records.

If the 12 November 2026 retest is already saved under the runtime checkout, the following assignments select that retest and its earlier attempts. After setting these paths, run the export block above.

```powershell
$runs = Join-Path $repo 'runs'
$liveRound1 = Join-Path $runs 'debate-rounds1-20261112-131824-132'
$liveRound2 = Join-Path $runs 'debate-rounds2-20261112-132637-039'
$batch = Join-Path $runs 'formal-pairs3-20261112-142618-024'
$earlierRuns = @(
    (Join-Path $runs 'debate-rounds1-20261112-131657-604'),
    (Join-Path $runs 'formal-pairs3-20261112-133419-596')
)
```

| Output | Contents |
| --- | --- |
| `results/trials.csv` | One row for each of the eight selected live trials. |
| `results/summary.csv` | Success counts, means, and sample variances by group for the eight selected trials. The two standalone runs have separate groups. Sample variance is `N/A` for fewer than two observations. |
| `results/attempt_inventory.csv` | Status and evidence path for each selected trial and any earlier attempts supplied to the exporter. |
| `results/formal_data_checks.csv` | Six checks of the recorded market reads, source hashes, and date cutoff for the paired batch. |
| `evidence/live-round1/`, `evidence/live-round2/`, and `evidence/three-pairs/` | Original `trial.json`, frozen-data evidence, effective configuration, and `report/complete_report.html` and `report/complete_report.md` for each selected trial. |

Open `complete_report.html` in a browser. The export goes into a new directory so the published results below remain available for comparison.

## Inspect or recalculate the recorded results

The recorded results are available without installing TradingAgents or using an API key.

| Artifact | What it contains |
| --- | --- |
| [Trial results](results/trials.csv) and [summary](results/summary.csv) | Per-attempt metrics and grouped statistics. The formal arms are `formal_frozen_one_round` and `formal_frozen_two_round`. |
| [Attempt inventory](results/attempt_inventory.csv) | Recorded successes, failures, and interruptions. |
| [Data checks](results/formal_data_checks.csv) | Frozen input and date cutoff checks for the completed trials. |
| [Completed campaign](evidence/3%20pairs%20experiments/) | Six trial records, effective settings, data-read traces, and reports. |
| [Earlier interrupted campaign](evidence/false%20experiment/) | The failed one-round trial before the completed campaign. |
| [Original pilot](evidence/original-pilot/) and [frozen two-round pilot](evidence/modification/) | Feasibility runs kept separate from the formal comparison. |

To review the published results, paste the entire block into Windows PowerShell from the `reproducibility` or `TradingAgents-runtime` folder. It uses standard Python and copies the six trial records and their complete reports to a new temporary folder. No API key is required.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $course = (Get-Location).Path
    if (-not (Test-Path -LiteralPath (Join-Path $course 'code\summarize_experiment.py') -PathType Leaf)) {
        $course = Join-Path $course '..\reproducibility'
    }

    $script = Join-Path $course 'code\summarize_experiment.py'
    $saved = Join-Path $course 'evidence\3 pairs experiments'
    if (-not (Test-Path -LiteralPath $script -PathType Leaf) -or
        -not (Test-Path -LiteralPath $saved -PathType Container)) {
        throw 'Open PowerShell in the reproducibility or TradingAgents-runtime folder.'
    }

    $trials = @(Get-ChildItem -Path "$saved\*\trial.json" -File)
    if ($trials.Count -ne 6) {
        throw "Expected six saved trial records, but found $($trials.Count)."
    }

    $review = Join-Path ([System.IO.Path]::GetTempPath()) ('tradingagents-review-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    New-Item -ItemType Directory -Path $review | Out-Null
    foreach ($trial in $trials) {
        $trialCopy = Join-Path $review $trial.Directory.Name
        New-Item -ItemType Directory -Path $trialCopy | Out-Null
        Copy-Item -LiteralPath $trial.FullName -Destination $trialCopy
        Copy-Item -LiteralPath (Join-Path $trial.DirectoryName 'report') -Destination $trialCopy -Recurse
    }

    python -B $script $review
    if ($LASTEXITCODE -ne 0) { throw 'Could not summarize the saved trials.' }

    Write-Host "Results folder: $review"
}
```

The output folder contains `trials.csv`, `summary.json`, and a folder for each trial. Open any trial's `report/complete_report.html` in a browser. For a fresh live run, look for the same report under that run's trial directory.

All six completed trials followed the expected turn counts. The two-round group took about 49.7 seconds longer on average, and the rating changed in one of the three pairs. Fresh calls can produce different arguments and ratings.

## Keep local files out of the submission

Keep the filled `.env`, the virtual environment, and fresh `runs/` output local. Review any evidence selected for publication to make sure it contains no API keys.
