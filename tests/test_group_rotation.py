"""Synthetic checks for generic research-group rotation; no effectiveness claim."""
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from radar_engine import research_group_rotation


class ResearchGroupRotationTests(unittest.TestCase):
    def test_all_groups_use_same_dynamic_rule(self):
        dates = pd.bdate_range("2026-08-03", periods=30)

        def frame(daily_return: float) -> pd.DataFrame:
            return pd.DataFrame(
                {"adj_close": 100.0 * np.power(1.0 + daily_return, np.arange(len(dates)))},
                index=dates,
            )

        frames = {
            "SPY": frame(0.001),
            "A1": frame(0.010),
            "A2": frame(0.008),
            "B1": frame(-0.006),
            "B2": frame(-0.004),
            "MU": frame(0.002),
            "SNDK": frame(0.0015),
            "ONLY": frame(0.030),
        }
        seeds = [
            {"symbol": "A1", "research_group": "强组"},
            {"symbol": "A2", "research_group": "强组"},
            {"symbol": "B1", "research_group": "弱组"},
            {"symbol": "B2", "research_group": "弱组"},
            {"symbol": "MU", "research_group": "存储与内存"},
            {"symbol": "SNDK", "research_group": "存储与内存"},
            {"symbol": "ONLY", "research_group": "单股组"},
        ]

        result = research_group_rotation(frames, seeds, dates[-1])

        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["rankings"]["5"]["strongest"][0]["research_group"], "强组")
        self.assertEqual(result["rankings"]["5"]["weakest"][0]["research_group"], "弱组")
        one = next(row for row in result["groups"] if row["research_group"] == "单股组")
        self.assertIsNone(one["relative_to_spy"]["5"])
        self.assertIn("全研究组采用同一规则", result["note"])
        self.assertNotIn("存储四只", " ".join(result["summary"]))


if __name__ == "__main__":
    unittest.main()
