"""Audit the existing environment and try one bounded, secret-safe API call.

This is a connectivity check, not a TradingAgents experiment. A failed check
exits nonzero; callers must not launch the pilot or paired runs after failure.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def error_chain(exc):
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        entry = {"type": type(exc).__name__}
        for field in ("errno", "winerror", "status_code"):
            value = getattr(exc, field, None)
            if isinstance(value, int):
                entry[field] = value
        chain.append(entry)
        exc = exc.__cause__ or exc.__context__
    return chain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--probe-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from dotenv import dotenv_values
    values = dotenv_values(args.env_file)
    key = values.get("DEEPSEEK_API_KEY")
    if args.probe_worker:
        logging.disable(logging.CRITICAL)
        from openai import OpenAI
        result = {"kind": "api_connectivity_probe", "started_at_utc": datetime.now(timezone.utc).isoformat(),
                  "provider": "deepseek", "base_url": "https://api.deepseek.com", "model": args.model,
                  "api_calls_attempted": 0, "sdk_max_retries": 0, "timeout_seconds": 15,
                  "failure_stage": "client_initialization",
                  "max_tokens": 64, "temperature": "not sent", "top_p": "not sent",
                  "thinking": "disabled", "prompt": "Reply with OK only.",
                  "http_status": None, "usage": None, "completed_responses": 0}
        started = time.perf_counter()
        try:
            with OpenAI(api_key=key, base_url=result["base_url"], max_retries=0, timeout=15.0) as client:
                result["api_calls_attempted"] = 1
                result["failure_stage"] = "completion_request"
                response = client.chat.completions.create(
                    model=args.model, messages=[{"role": "user", "content": result["prompt"]}],
                    max_tokens=64, extra_body={"thinking": {"type": "disabled"}},
                )
                result.update(status="ok", http_status=200, completed_responses=1,
                              failure_stage=None,
                              response_model=response.model,
                              response_present=bool(response.choices),
                              usage=response.usage.model_dump() if response.usage else None)
        except Exception as exc:
            result.update(status="failed", exception_chain=error_chain(exc),
                          http_status=getattr(exc, "status_code", None))
        result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        print(json.dumps(result), flush=True)
        return int(result["status"] != "ok")

    args.output.mkdir(parents=True, exist_ok=False)
    secrets = [v for k, v in values.items() if v and any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET"))]
    example = args.source / ".env.example"
    example_values = dotenv_values(example) if example.exists() else {}
    secrets.extend(v for k, v in example_values.items() if v and any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET")))

    def redact(text):
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)

    import tradingagents
    from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
    dependencies = {}
    for name in ("openai", "httpx", "langchain-core", "langchain-openai", "langgraph", "yfinance", "pandas", "python-dotenv", "pytest"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    instructions = []
    for root in (args.source, ROOT):
        for directory, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in (".venv", ".git", "__pycache__", "runs", "node_modules")]
            if "AGENTS.md" in files:
                path = Path(directory) / "AGENTS.md"
                instructions.append({"path": str(path), "content": redact(path.read_text(encoding="utf-8-sig"))})
    meta = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
            "executable": sys.executable, "platform": platform.platform(), "cpu": platform.processor(),
            "logical_cpu_count": os.cpu_count(), "compute": "CPU orchestration; hosted LLM API; no local GPU model",
            "project_version": tradingagents.__version__, "import_path": str(Path(tradingagents.__file__).resolve()),
            "source": str(args.source), "working_copy": str(ROOT),
            "source_has_git": (args.source / ".git").exists(), "working_copy_has_git": (ROOT / ".git").exists(),
            "key_file": str(args.env_file), "deepseek_key_present": bool(key),
            "key_source": "explicit dotenv file (takes precedence for this probe)",
            "dotenv_example_contains_nonempty_key": bool(example_values.get("DEEPSEEK_API_KEY")),
            "default_logs_path": str(Path.home() / ".tradingagents/logs"),
            "default_logs_exists": (Path.home() / ".tradingagents/logs").exists(),
            "dependencies": dependencies, "nested_instructions": instructions, "import_status": "ok"}
    write_json(args.output / "environment.json", meta)
    manifest = []
    for prefix in ("tradingagents", "cli", "tests"):
        for path in sorted((ROOT / prefix).rglob("*.py")):
            rel = path.relative_to(ROOT)
            original = args.source / rel
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            old_digest = hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None
            manifest.append({"path": rel.as_posix(), "working_sha256": digest,
                             "source_sha256": old_digest, "identical": digest == old_digest})
    write_json(args.output / "source_manifest.json", manifest)
    read_files = ["README.md", "pyproject.toml", "cli/main.py", "tradingagents/backtest.py",
                  "tradingagents/default_config.py", "tradingagents/graph/conditional_logic.py",
                  "tests/test_debate_opening.py", "tests/test_backtest.py"]
    write_json(args.output / "reviewed_sources.json", [
        {"path": rel, "sha256": hashlib.sha256((args.source / rel).read_bytes()).hexdigest()}
        for rel in read_files if (args.source / rel).exists()])
    commands = []
    child_env = os.environ.copy()
    child_env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", TRADINGAGENTS_NO_UPDATE_CHECK="1")
    cli_cmd = [sys.executable, "-B", "-m", "cli.main", "--help"]
    cli = subprocess.run(cli_cmd, cwd=ROOT, env=child_env, capture_output=True, timeout=45)
    (args.output / "cli-help.txt").write_text(redact((cli.stdout + cli.stderr).decode("utf-8", errors="replace")), encoding="utf-8")
    commands.append({"argv": cli_cmd, "exit_code": cli.returncode})
    print(json.dumps({"version": meta["project_version"], "python": platform.python_version(),
                      "key_present": bool(key), "cli_exit_code": cli.returncode}), flush=True)
    if cli.returncode or not key:
        write_json(args.output / "commands.json", commands)
        write_json(args.output / "stop.json", {"reason": "cli_failed_or_key_missing", "experiments_started": False})
        return 2
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "--source", str(args.source),
           "--env-file", str(args.env_file), "--output", str(args.output), "--model", args.model, "--probe-worker"]
    started = time.perf_counter()
    with subprocess.Popen(cmd, cwd=ROOT, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=45)
            probe = json.loads(stdout.decode("utf-8").strip())
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            probe = {"status": "failed", "error_type": "ProbeWallTimeLimit", "usage": None,
                     "elapsed_seconds": round(time.perf_counter() - started, 3), "http_status": None,
                     "completed_responses": 0, "worker_terminated": True}
        except (ValueError, UnicodeDecodeError):
            probe = {"status": "failed", "error_type": "ProbeWorkerFailed", "http_status": None, "usage": None}
        commands.append({"argv": cmd, "pid": proc.pid, "exit_code": proc.returncode,
                         "process_exited": proc.poll() is not None})
    write_json(args.output / "api_probe.json", probe)
    write_json(args.output / "commands.json", commands)
    if probe["status"] != "ok":
        dns = []
        for host in ("api.deepseek.com", "query1.finance.yahoo.com"):
            try:
                socket.getaddrinfo(host, 443)
                dns.append({"host": host, "status": "resolved"})
            except OSError as exc:
                dns.append({"host": host, "status": "failed", "exception_chain": error_chain(exc)})
        write_json(args.output / "dns.json", dns)
        write_json(args.output / "stop.json", {
            "reason": "API call failed; stopped at user's instruction", "experiments_started": False,
            "paired_trials_started": 0, "new_offline_trials_started": 0, "retry_count": 0,
            "probe_process_exited": True, "stopped_at_utc": datetime.now(timezone.utc).isoformat()})
    print(json.dumps(probe), flush=True)
    return int(probe["status"] != "ok")


if __name__ == "__main__":
    raise SystemExit(main())
