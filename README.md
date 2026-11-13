# TradingAgents Debate Reproduction

This project compares one and two rounds of Bull and Bear research debate in TradingAgents. Both settings use the market analyst, an analysis date of 9 November 2026, and one round of risk discussion. Each trial starts with empty memory. Both model tiers use DeepSeek `deepseek-flash` through the DeepSeek API.

| Setting | Baseline | Treatment |
| --- | --- | --- |
| Research debate | 1 round, 2 turns | 2 rounds, 4 turns |
| Risk discussion | 1 round, 3 turns | 1 round, 3 turns |

The study records time, model calls, tokens, and ratings for a single NVDA task. These measurements do not reproduce the paper's investment returns. [TradingAgents, arXiv:2412.20138v7](https://arxiv.org/abs/2412.20138v7). 

## Install on Windows

Install Git and Python 3.12. Open PowerShell in a folder where you want to keep the checkout, then run the following commands. If you already cloned the course repository, open PowerShell in that checkout and start with the setup command.

```powershell
git clone https://github.com/milala-lala/reproducibility.git
if ($LASTEXITCODE -ne 0) { throw 'The course repository could not be cloned.' }
Set-Location -LiteralPath '.\reproducibility'
```

Run the remaining commands from the course checkout. Check that `python --version` reports Python 3.12. If your launcher is `py` instead, use `py -3.12 -B .\code\reproduce.py setup`.

```powershell
python -B .\code\reproduce.py setup
```

[Setup](code/reproduce.py) fetches the TradingAgents 0.7.1 revision `3e44faafa042daf953f6ec2422ae36bb1fd4f50f` into `.tradingagents-runtime/`, creates a virtual environment there, and installs the project using [the observed dependency versions](requirements-observed.txt) as constraints. The remaining versions are resolved by pip. Setup also installs the course scripts in the runtime checkout. The commands use its Python executable directly, so activating the virtual environment is optional.

## Check the workflow offline

```powershell
& '.\.tradingagents-runtime\.venv\Scripts\python.exe' -B .\code\reproduce.py offline
```

This runs the control tests and two scripted trials. In the printed output directory, the one-round trial should record 2 research turns and 3 risk turns. The two-round trial should record 4 research turns and 3 risk turns. The tests and scripted trials use synthetic prices and require no API key.

## Configure DeepSeek

Setup creates a local `.env` from the [blank template](env.template) when the file does not already exist. Open it in an editor, enter your own value for `DEEPSEEK_API_KEY`, then save it.

```powershell
notepad.exe '.\.tradingagents-runtime\.env'
```

The live campaign needs a DeepSeek API key with sufficient balance, access to Yahoo Finance, and a connection that can sustain long model responses. One short DeepSeek preflight request runs before the trials.

## Run both standalone trials and three pairs

```powershell
& '.\.tradingagents-runtime\.venv\Scripts\python.exe' -B .\code\reproduce.py live
```

The command downloads daily NVDA OHLCV data through `yfinance` for 10 November 2021 through 9 November 2026. It saves the CSV and `snapshot_source.json`, which records the source, date range, row count, and SHA-256 hash. All eight trials in the campaign use that same file. A newly downloaded snapshot can differ from the one used for the submitted report, and live model responses can vary.

The command runs the one-round and two-round standalone trials first. It then runs three pairs in the order 1/2, 2/1, 1/2. The standalone trials have separate summary groups. When a request or trial fails, the command stops and retains the files already written under the printed campaign directory. Check `preflight/api_probe.json` for a failed preflight or `trial.json` and `run.log` in the last trial directory for a failed experiment. A complete export is written after all eight trials succeed.

## Find the evidence and result tables

A completed campaign prints the absolute path of its `export` directory. The raw runs, downloaded prices and preflight logs remain in the same campaign directory.

```text
runs/live-<timestamp>/
├── input/
├── preflight/
├── live-round1/
├── live-round2/
├── three-pairs/
└── export/
    ├── evidence/
    │   ├── live-round1/pilot/
    │   ├── live-round2/pilot/
    │   └── three-pairs/
    │       ├── pair-1-rounds-1/
    │       ├── pair-1-rounds-2/
    │       ├── pair-2-rounds-1/
    │       ├── pair-2-rounds-2/
    │       ├── pair-3-rounds-1/
    │       └── pair-3-rounds-2/
    └── results/
        ├── trials.csv
        ├── summary.csv
        ├── attempt_inventory.csv
        └── formal_data_checks.csv
```

Each of the eight exported trial directories contains `trial.json`, `data_manifest.json`, `data_reads.jsonl`, `effective_config.json`, and both `report/complete_report.html` and `report/complete_report.md`. Open the HTML in a browser or read the Markdown in an editor.

| File | Contents |
| --- | --- |
| `trials.csv` | Eight per-trial records with turn counts, latency, model usage, and final ratings. |
| `summary.csv` | Successful and failed counts, metric means, sample variances, and sample sizes by group. Sample variance is `N/A` for fewer than two observations. |
| `attempt_inventory.csv` | Status and evidence path for every exported trial and any explicitly supplied earlier attempts. |
| `formal_data_checks.csv` | Market-data hashes, recorded price reads, and the cutoff check for the six paired trials. |


