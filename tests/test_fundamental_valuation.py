import unittest
import tempfile
from pathlib import Path

from scripts.fundamental_valuation import _parse_yearly_rows, build_valuation_bounds, fetch_consensus_snapshot


def forecast(consensus, low=None, high=None, count=6):
    return {
        "status": "observed",
        "source_url": "https://api.nasdaq.com/example",
        "yearly_forecasts": [{
            "fiscal_end": "Dec 2027",
            "eps_consensus": consensus,
            "eps_low": consensus if low is None else low,
            "eps_high": consensus if high is None else high,
            "estimate_count": count,
            "revisions_up_4w": 1,
            "revisions_down_4w": 0,
        }],
    }


class FundamentalValuationTests(unittest.TestCase):
    def test_nasdaq_yearly_consensus_parser_preserves_range_and_support(self):
        payload = {"data": {"yearlyForecast": {"rows": [{
            "fiscalEnd": "Jun 2027", "consensusEPSForecast": 19.78,
            "lowEPSForecast": 17.45, "highEPSForecast": 21.15,
            "noOfEstimates": 8, "up": 2, "down": 1,
        }]}}}
        row = _parse_yearly_rows(payload)[0]
        self.assertEqual(row["fiscal_end"], "Jun 2027")
        self.assertEqual(row["estimate_count"], 8)
        self.assertEqual((row["eps_low"], row["eps_consensus"], row["eps_high"]), (17.45, 19.78, 21.15))

    def test_theoretical_bounds_use_ex_self_peer_forward_pe_quartiles(self):
        records = [{"symbol": "TARGET", "reference_price": 100, "research_group": "G", "coverage_bucket": "T"}]
        snapshot_records = {"TARGET": forecast(10, 8, 12)}
        for symbol, pe in zip(("A", "B", "C", "D"), (8, 10, 12, 14)):
            records.append({"symbol": symbol, "reference_price": pe * 10, "research_group": "G", "coverage_bucket": "T"})
            snapshot_records[symbol] = forecast(10)
        result = build_valuation_bounds(records, {
            "as_of": "2026-10-12", "retrieved_at": "2026-10-12T22:30:00Z",
            "provider": "test", "records": snapshot_records,
        })["records"]["TARGET"]
        self.assertEqual(result["status"], "observed")
        self.assertAlmostEqual(result["peer_forward_pe_p25"], 9.5)
        self.assertAlmostEqual(result["peer_forward_pe_p50"], 11.0)
        self.assertAlmostEqual(result["peer_forward_pe_p75"], 12.5)
        self.assertAlmostEqual(result["theoretical_lower"], 76.0)
        self.assertAlmostEqual(result["theoretical_central"], 110.0)
        self.assertAlmostEqual(result["theoretical_upper"], 150.0)
        self.assertEqual(result["probability_integration_status"], "DISPLAY_ONLY_PENDING_ARCHIVED_CONSENSUS_BACKTEST")

    def test_nonpositive_eps_is_explicitly_unavailable_for_pe_method(self):
        records = [{"symbol": "LOSS", "reference_price": 20, "research_group": "G", "coverage_bucket": "T"}]
        result = build_valuation_bounds(records, {"records": {"LOSS": forecast(-1)}})["records"]["LOSS"]
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "nonpositive_eps_consensus_requires_non_pe_method")

    def test_peer_multiple_is_guarded_against_structural_business_mix_gap(self):
        records = [{"symbol": "TARGET", "reference_price": 50, "research_group": "G", "coverage_bucket": "T"}]
        snapshot_records = {"TARGET": forecast(10, 8, 12)}  # own implied forward P/E = 5x
        for symbol, pe in zip(("A", "B", "C", "D"), (20, 25, 30, 35)):
            records.append({"symbol": symbol, "reference_price": pe * 10, "research_group": "G", "coverage_bucket": "T"})
            snapshot_records[symbol] = forecast(10)
        result = build_valuation_bounds(records, {"records": snapshot_records})["records"]["TARGET"]
        self.assertAlmostEqual(result["own_forward_pe_guard_high"], 6.25)
        self.assertAlmostEqual(result["applied_forward_pe_lower"], 6.25)
        self.assertAlmostEqual(result["applied_forward_pe_central"], 6.25)
        self.assertAlmostEqual(result["applied_forward_pe_upper"], 6.25)
        self.assertAlmostEqual(result["theoretical_lower"], 50.0)
        self.assertAlmostEqual(result["theoretical_upper"], 75.0)

    def test_current_consensus_cannot_backfill_a_historical_market_date(self):
        with tempfile.TemporaryDirectory() as folder:
            result = fetch_consensus_snapshot(["LITE"], "2000-01-03", Path(folder), refresh=True)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["reason"], "historical_consensus_backfill_forbidden")
            self.assertFalse(list(Path(folder).rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
