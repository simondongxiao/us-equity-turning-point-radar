import unittest

from scripts.audit_published_run import daily_runtime_checks


class PublishedRunAuditTests(unittest.TestCase):
    def _payload(self):
        records = []
        intraday_symbols = {}
        for index in range(100):
            symbol = f"T{index:03d}"
            records.append(
                {
                    "symbol": symbol,
                    "metrics": {
                        str(horizon): {"status": "calibrated"}
                        for horizon in (5, 10, 21)
                    },
                }
            )
            intraday_symbols[symbol] = {
                "status": "matched",
                "date": "2026-10-09",
                "bar_count": 79,
                "regular_session_aggregate": {"close": 100.0},
            }
        return {
            "run_id": "radar-20261009-test",
            "as_of": "2026-10-09",
            "records": records,
            "source_manifest": {
                "complete_session_cutoff_ny": "2026-10-09",
                "common_latest_date": "2026-10-09",
                "intraday_regular_session_validation": {
                    "symbols": intraday_symbols,
                    "unavailable": 0,
                },
            },
            "boundary_breach_alerts": {
                "status": "observed",
                "version": "prior-frozen-tiered-boundary-breach-v1.6.1",
                "basis_as_of": "2026-10-08",
                "evaluated_symbol_horizons": 300,
                "unavailable_symbol_horizons": 0,
                "records": [],
                "tiered_records": [
                    {
                        "side": "above_upper",
                        "observed_price": 101.0,
                        "boundary_value": 100.0,
                    }
                ],
            },
            "index_forecasts": {"records": [{} for _ in range(13)]},
            "decision_board": {
                "horizons": {
                    str(horizon): {
                        "stage_bottom_candidates": [],
                        "stage_top_warnings": [],
                    }
                    for horizon in (5, 10, 21)
                }
            },
        }

    def test_complete_daily_run_passes_all_runtime_checks(self):
        payload = self._payload()
        html = "radar-20261009-test 价格越界预警 决策榜"
        checks = daily_runtime_checks(payload, payload["run_id"], html)
        self.assertTrue(checks)
        self.assertTrue(all(check["status"] == "PASS" for check in checks))

    def test_same_day_boundary_basis_and_bad_relation_fail(self):
        payload = self._payload()
        payload["boundary_breach_alerts"]["basis_as_of"] = payload["as_of"]
        payload["boundary_breach_alerts"]["tiered_records"][0]["observed_price"] = 99.0
        html = "radar-20261009-test 价格越界预警 决策榜"
        checks = daily_runtime_checks(payload, payload["run_id"], html)
        boundary = next(check for check in checks if "冻结边界" in check["item"])
        self.assertEqual(boundary["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
