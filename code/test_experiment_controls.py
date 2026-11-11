"""Offline checks for the scientific controls. No API key or network is used."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment_support import frozen_market, redact, EvidenceStats
from tradingagents.graph.conditional_logic import ConditionalLogic


class Controls(unittest.TestCase):
    def test_routing_boundary_and_alternation(self):
        for rounds in (1,2):
            logic=ConditionalLogic(rounds,1)
            state={'investment_debate_state':{'count':2*rounds-1,'current_response':'Bull: opening'}}
            self.assertEqual(logic.should_continue_debate(state),'Bear Researcher')
            state['investment_debate_state']['count']+=1
            self.assertEqual(logic.should_continue_debate(state),'Research Manager')
            self.assertEqual(logic.should_continue_risk_analysis({'risk_debate_state':{'count':3}}),'Portfolio Manager')

    def test_frozen_snapshot_and_history_read_actual_csv_exclude_future(self):
        from tradingagents.dataflows.vendors.yahoo import market,snapshot
        from tradingagents.graph import trading_graph
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); raw=root/'raw.csv'
            raw.write_text('Date,Open,High,Low,Close,Volume\n2026-11-06,100,102,99,101,1000\n2026-11-09,101,103,100,102,1100\n2026-11-10,998,1000,997,999,1200\n')
            out=root/'output'; out.mkdir()
            # Any accidental network request in this check fails immediately.
            with patch('socket.socket.connect',side_effect=AssertionError('Network not permitted in validation')):
                with frozen_market(raw,out):
                    a=snapshot.build_verified_market_snapshot('NVDA','2026-11-09')
                    b=market.get_YFin_data_online('NVDA','2026-11-06','2026-11-09')
                    self.assertRegex(a,r'\| Close \| 102(?:\.00)? \|'); self.assertIn('102.00',b)
                    self.assertNotIn('| Close | 999',a); self.assertNotIn('2026-11-10',b)
                    self.assertEqual(trading_graph.resolve_instrument_identity('NVDA')['company_name'],'NVIDIA Corporation')
            reads=[json.loads(x) for x in (out/'data_reads.jsonl').read_text().splitlines()]
            self.assertEqual({r['route'] for r in reads},{'load_ohlcv','Ticker.history'})
            self.assertTrue(all(r['latest_returned_date']=='2026-11-09' for r in reads))

    def test_unknown_usage_not_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            stats=EvidenceStats(temp)
            stats.on_chat_model_start({},[],run_id='r1')
            stats.on_llm_error(ConnectionError(),run_id='r1')
            result=stats.get_stats()
            self.assertEqual(result['llm_calls'],1)
            self.assertEqual(result['llm_successful_calls'],0)
            self.assertIsNone(result['tokens_in'])
            self.assertEqual(result['llm_calls_without_returned_usage'],1)

    def test_redaction(self):
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':'unit_test_secret_placeholder'}):
            self.assertNotIn('unit_test_secret_placeholder',redact('unit_test_secret_placeholder'))
        self.assertNotIn('secret-text',redact('Authorization: Bearer secret-text'))


if __name__=='__main__': unittest.main(verbosity=2)
