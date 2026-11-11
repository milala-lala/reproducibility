"""Run a paired debate-round experiment against the installed TradingAgents pipeline."""

import argparse
import copy
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parent
TICKER, TRADE_DATE = "NVDA", "2026-11-09"


def run_trial(out, rounds, offline, frozen_csv=None):
    from experiment_support import EvidenceStats, EvidenceLog, frozen_market, write_json
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(
        results_dir=str(out / "logs"),
        data_cache_dir=str(out / "cache"),
        memory_log_path=str(out / "memory.md"),
        llm_provider="deepseek",
        quick_think_llm="deepseek-flash",
        deep_think_llm="deepseek-flash",
        quick_think_provider=None,
        deep_think_provider=None,
        quick_think_backend_url=None,
        deep_think_backend_url=None,
        backend_url="https://api.deepseek.com",
        max_debate_rounds=rounds,
        max_risk_discuss_rounds=1,
        max_tool_rounds=4,
        checkpoint_enabled=False,
        output_language="English",
        temperature=None,
        max_tokens=None,
        llm_max_retries=0,
    )
    # This config.update overrides dotenv/default_config for the experiment knobs.
    write_json(out / 'effective_config.json', {
        'config': config, 'analysts': ['market'], 'initial_memory': 'empty',
        'import_path': str(Path(sys.modules['tradingagents'].__file__).resolve()),
        'script_path': str(Path(__file__).resolve()),
        'sampling': {'temperature': 'not explicitly set', 'top_p': 'not explicitly set',
                     'max_tokens': 'not explicitly set', 'seed': 'not explicitly set'},
        'frozen_csv': str(frozen_csv.resolve()) if frozen_csv else None,
    })
    assert not Path(config['memory_log_path']).exists()
    stats = EvidenceStats(out)
    handler = EvidenceLog(out)
    logging.getLogger().addHandler(handler)
    evidence = {
        "mode": "offline_scripted" if offline else "live",
        "ticker": "NVDA",
        "date": "2026-01-09" if offline else TRADE_DATE,
        "debate_rounds_configured": config['max_debate_rounds'],
        "risk_rounds_configured": config['max_risk_discuss_rounds'],
        "provider": "scripted" if offline else config["llm_provider"],
        "model": "local deterministic fixture" if offline else config["quick_think_llm"],
        "base_url": None if offline else config["backend_url"],
        "temperature": "provider default",
        "top_p": "provider default",
        "max_tokens": "provider default",
        "initial_memory": "empty",
        "analysts": ["market"],
        "cohort": "offline_validation" if offline else ("frozen_raw_v1" if frozen_csv else "live_vendor_pilot"),
    }
    started = time.perf_counter()
    try:
        with frozen_market(frozen_csv, out, TRADE_DATE) if frozen_csv else nullcontext(None) as data_info:
            if data_info:
                evidence['data_sha256'] = data_info['consumed_frame_sha256']
            graph = TradingAgentsGraph(selected_analysts=("market",), config=config, callbacks=[stats])
            write_json(out / 'model_runtime.json', {
                tier: {k: getattr(model,k,None) for k in ('model_name','temperature','top_p','max_tokens','max_retries','request_timeout')}
                for tier,model in [('quick',graph.quick_thinking_llm),('deep',graph.deep_thinking_llm)]
            })
            state, rating = graph.propagate(evidence["ticker"], evidence["date"])
            if data_info:
                reads = out / 'data_reads.jsonl'
                if not reads.is_file() or not reads.stat().st_size:
                    raise RuntimeError('No frozen market data read was recorded')
        debate = state["investment_debate_state"]
        risk = state["risk_debate_state"]
        evidence.update(
            status="ok",
            elapsed_seconds=round(time.perf_counter() - started, 3),
            debate_turns=debate["count"],
            risk_turns=risk["count"],
            rating=rating,
            market_report_sha256=hashlib.sha256(state["market_report"].encode()).hexdigest(),
        )
        graph.save_reports(state, TICKER, out / "report")
        (out / "debate.txt").write_text(debate["history"], encoding="utf-8")
        write_json(out / 'observed_counts.json', {'research_turns': debate['count'], 'risk_turns': risk['count'],
                   'research_expected': 2*config['max_debate_rounds'], 'risk_expected': 3*config['max_risk_discuss_rounds']})
        assert debate['count'] == 2*config['max_debate_rounds']
        assert risk['count'] == 3*config['max_risk_discuss_rounds']
    except Exception as exc:
        # Avoid persisting exception bodies which may include provider credentials.
        evidence.update(status="failed", error_type=type(exc).__name__,
                        elapsed_seconds=round(time.perf_counter() - started, 3))
        cause = exc.__cause__ or exc.__context__
        if cause:
            evidence["cause_type"] = type(cause).__name__
        if getattr(exc, "status_code", None) is not None:
            evidence["http_status"] = exc.status_code
        chain=[]; seen=set(); error=exc
        while error is not None and id(error) not in seen:
            seen.add(id(error)); chain.append(type(error).__name__)
            error=error.__cause__ or error.__context__
        evidence['exception_chain']=chain
    finally:
        logging.getLogger().removeHandler(handler)
    evidence['fallback_count']=handler.fallback_count
    evidence.update(stats.get_stats())
    with (out/'run.log').open('a',encoding='utf-8') as log:
        log.write(json.dumps(evidence)+'\n')
    (out / "trial.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence), flush=True)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Existing local dotenv file; never copied.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, choices=(0, 3, 4, 5), default=0)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--debate-rounds", type=int, choices=(1,2), default=1,
                        help="Single live pilot research rounds; risk stays 1. --pairs always compares 1 and 2.")
    parser.add_argument("--frozen-csv", type=Path, help="Original NVDA raw OHLCV CSV; freezes all price reads to 2026-11-09.")
    args = parser.parse_args()
    if args.offline and args.pairs:
        parser.error("Use --offline without --pairs for the deterministic fixture check.")
    if args.offline and args.frozen_csv:
        parser.error('The offline graph check uses synthetic data; do not combine it with --frozen-csv.')
    if args.pairs and not args.frozen_csv:
        parser.error('Formal pairs require --frozen-csv so both arms consume identical raw data.')
    if args.frozen_csv and not args.frozen_csv.is_file():
        parser.error('--frozen-csv must be an existing raw CSV file.')
    if args.env_file and not args.offline:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=True)
    if not args.offline and not os.getenv("DEEPSEEK_API_KEY"):
        parser.error("Set DEEPSEEK_API_KEY locally or provide --env-file.")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'command.json').write_text(json.dumps({
        'python':sys.executable,'argv':sys.argv,'cwd':str(Path.cwd()),
        'pair_order': [([1, 2] if pair % 2 else [2, 1]) for pair in range(1, args.pairs + 1)] or None,
    },indent=2)+'\n',encoding='utf-8')

    if args.offline:
        from contextlib import ExitStack
        from unittest.mock import patch

        import pandas as pd
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        from tradingagents.agents import context
        from tradingagents.dataflows.vendors.yahoo import market, snapshot
        from tradingagents.graph import trading_graph

        class ScriptedModel(BaseChatModel):
            calls: list

            @property
            def _llm_type(self):
                return "scripted_fixture"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                self.calls.append(1)
                message = AIMessage(content="Fixture report.\n\n**Rating**: Overweight\n\nFINAL TRANSACTION PROPOSAL: **BUY**")
                return ChatResult(generations=[ChatGeneration(message=message)])

        class FixtureClient:
            def __init__(self, model):
                self.model = model

            def get_llm(self):
                return self.model

        prices = pd.DataFrame({
            "Date": pd.bdate_range(end="2026-01-09", periods=60),
            "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1_000_000,
        })
        for rounds in (1, 2):
            with ExitStack() as mocks:
                context._identity.cache_clear()
                model = ScriptedModel(calls=[])
                mocks.enter_context(patch.object(trading_graph, "create_tier_client", return_value=FixtureClient(model)))
                mocks.enter_context(patch.object(snapshot, "load_ohlcv", side_effect=lambda *a, **k: prices.copy()))
                mocks.enter_context(patch.object(market.yf, "Ticker", return_value=type("T", (), {"info": {"longName": "NVIDIA"}})()))
                out = args.output / f"rounds-{rounds}"
                result = run_trial(out, rounds, offline=True)
                result["scripted_model_invocations"] = len(model.calls)
                result["llm_calls"] = result["tool_calls"] = None
                result["tokens_in"] = result["tokens_out"] = None
                (out / "trial.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                if result["status"] != "ok":
                    return 1
                assert result["debate_turns"] == 2 * rounds
                assert result["risk_turns"] == 3
        return 0

    if not args.pairs:
        return int(run_trial(args.output / "pilot", args.debate_rounds, offline=False,
                             frozen_csv=args.frozen_csv)["status"] != "ok")
    for pair in range(1, args.pairs + 1):
        for rounds in ((1, 2) if pair % 2 else (2, 1)):
            result = run_trial(args.output / f"pair-{pair}-rounds-{rounds}", rounds, offline=False,
                               frozen_csv=args.frozen_csv)
            if result["status"] != "ok":
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
