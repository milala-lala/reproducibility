"""Evidence collection and optional frozen raw OHLCV replay; no model-output replay."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
from unittest.mock import patch

from cli.stats_handler import StatsCallbackHandler


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def redact(value):
    text = str(value)
    for name, secret in os.environ.items():
        if any(x in name.upper() for x in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and len(secret) >= 8:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)\bsk-[a-z0-9_-]{12,}', '[REDACTED]', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)[^\r\n]+', r'\1[REDACTED]', text)
    return re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)


def safe_config(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if any(s in k.lower() for s in ('api_key', 'password', 'authorization', 'secret'))
                    else safe_config(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [safe_config(v) for v in value]
    if isinstance(value, str): return redact(value)
    if value is None or isinstance(value, (bool, int, float)): return value
    return type(value).__name__


def write_json(path, value):
    Path(path).write_text(json.dumps(safe_config(value), indent=2) + '\n', encoding='utf-8')


class EvidenceStats(StatsCallbackHandler):
    """Count lifecycle events without storing prompts, headers, keys or error bodies."""
    def __init__(self, out):
        super().__init__()
        self.out = Path(out)
        self.event_lock = threading.Lock()
        self.started, self.ended, self.failed, self.used = set(), set(), set(), set()

    def event(self, name, run_id, **data):
        with self.event_lock:
            row = {'utc': datetime.now(timezone.utc).isoformat(), 'event': name,
                   'run_id': str(run_id), **data}
            with (self.out / 'llm_events.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(row) + '\n')

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._start(kwargs.get('run_id'))

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._start(kwargs.get('run_id'))

    def _start(self, rid):
        with self._lock:
            self.started.add(str(rid)); self.llm_calls = len(self.started)
        self.event('start', rid)

    def on_llm_end(self, response, **kwargs):
        rid = str(kwargs.get('run_id'))
        with self._lock:
            if rid in self.ended: return
            self.ended.add(rid)
        super().on_llm_end(response, **kwargs)
        usage = None
        try: usage = response.generations[0][0].message.usage_metadata
        except (IndexError, AttributeError, TypeError): pass
        if usage:
            with self._lock: self.used.add(rid)
        self.event('end', rid, usage_available=bool(usage),
                   input_tokens=usage.get('input_tokens') if usage else None,
                   output_tokens=usage.get('output_tokens') if usage else None)

    def on_llm_error(self, error, **kwargs):
        rid = str(kwargs.get('run_id'))
        with self._lock: self.failed.add(rid)
        self.event('error', rid, error_type=type(error).__name__)

    def get_stats(self):
        d = super().get_stats()
        d.update(llm_successful_calls=len(self.ended), llm_error_callbacks=len(self.failed),
                 llm_calls_with_usage=len(self.used), llm_calls_without_returned_usage=len(self.started-self.used),
                 usage_complete=bool(self.started) and self.started == self.used)
        if not self.used: d['tokens_in'] = d['tokens_out'] = None
        return d


class EvidenceLog(logging.Handler):
    def __init__(self, out):
        super().__init__(logging.WARNING)
        self.path = Path(out) / 'run.log'
        self.fallback_count = 0

    def emit(self, record):
        message = redact(record.getMessage())
        if 'retrying once as free text' in message: self.fallback_count += 1
        # Do not include traceback locals or request objects.
        with self.path.open('a', encoding='utf-8') as f:
            f.write(f'{record.levelname} {record.name}: {message}\n')


@contextmanager
def frozen_market(csv_path, out, cutoff='2026-11-09'):
    """Replace Yahoo reads with the actual CSV, preserving analyst/tool execution.

    All price paths use the same cutoff. No HTTP request is used to obtain
    price or identity inputs. This does not alter or replace model outputs.
    Both formal arms must use this adapter; the historical pilot is separate.
    """
    import pandas as pd
    from tradingagents.agents import context
    from tradingagents.dataflows.vendors.yahoo import market, ohlcv, snapshot
    from tradingagents.graph import trading_graph

    source = Path(csv_path).resolve()
    out = Path(out)
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    frame = ohlcv._clean_dataframe(pd.read_csv(source)).sort_values('Date')
    frame = frame[frame.Date <= pd.Timestamp(cutoff)].copy()
    if frame.empty: raise ValueError('Frozen source has no rows on or before cutoff')
    ohlcv._assert_ohlcv_not_stale(frame, cutoff, 'NVDA', 'NVDA')
    cache = out / 'cache'; cache.mkdir(parents=True, exist_ok=True)
    (cache / 'source-NVDA-YFin-data.csv').write_bytes(source_bytes)
    filtered = cache / 'NVDA-frozen-to-cutoff.csv'
    frame.to_csv(filtered, index=False)
    input_hash = sha256(filtered)
    identity = {'company_name': 'NVIDIA Corporation'}
    metadata = {'mode':'frozen_raw_ohlcv', 'source_path':str(source), 'source_sha256':source_hash,
                'consumed_frame_sha256':input_hash, 'rows':len(frame),
                'start':str(frame.Date.min().date()), 'end':str(frame.Date.max().date()),
                'cutoff':cutoff, 'provenance':'Frozen CSV supplied by the runner; see source_path and source_sha256',
                'identity':identity, 'identity_provenance':'Fixed symbol identity for formal comparison; original prompt identity not saved',
                'note':'Fresh analyst and model calls; market_report is never loaded from the pilot.'}
    write_json(out / 'data_manifest.json', metadata)
    read_lock = threading.Lock()
    def record(route, data):
        with read_lock:
            row={'route':route,'source_sha256':source_hash,'consumed_frame_sha256':input_hash,
                 'returned_rows':len(data),'latest_returned_date':str(data.Date.max().date()) if len(data) else None,
                 'returned_frame_sha256':hashlib.sha256(data.to_csv(index=False).encode('utf-8')).hexdigest()}
            with (out/'data_reads.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(row)+'\n')
    def load(symbol, as_of_date, fill_gaps=True):
        if symbol.upper()!='NVDA': raise ValueError('Frozen protocol supports NVDA only')
        data=frame[frame.Date <= min(pd.Timestamp(as_of_date),pd.Timestamp(cutoff))].copy()
        data=ohlcv._fill_price_gaps(data) if fill_gaps else data.dropna(subset=['Close']).copy()
        record('load_ohlcv',data)
        return data
    class FrozenTicker:
        def __init__(self,symbol):
            if symbol.upper()!='NVDA': raise ValueError('Frozen protocol supports NVDA only')
        @property
        def info(self): return {'longName': identity['company_name']}
        def history(self,start=None,end=None,**kwargs):
            data=frame.copy()
            if start is not None: data=data[data.Date>=pd.Timestamp(start)]
            if end is not None: data=data[data.Date<pd.Timestamp(end)]
            record('Ticker.history',data)
            return data.set_index('Date')
    with ExitStack() as stack:
        for module in (ohlcv,market,snapshot): stack.enter_context(patch.object(module,'load_ohlcv',side_effect=load))
        stack.enter_context(patch.object(market.yf,'Ticker',FrozenTicker))
        stack.enter_context(patch.object(trading_graph,'resolve_instrument_identity',return_value=identity))
        context._identity.cache_clear()
        try: yield metadata
        finally: context._identity.cache_clear()
