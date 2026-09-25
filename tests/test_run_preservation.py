"""Regression checks for frozen run preservation; no financial assertions."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import radar_engine as engine


class RunPreservationTests(unittest.TestCase):
    def test_existing_run_rejected_before_data_fetch(self):
        with tempfile.TemporaryDirectory(dir=engine.OUTPUT_DIR) as folder:
            out = Path(folder)
            frozen = out / "dashboard-frozen-run.json"
            frozen.write_text('{"original":true}', encoding="utf-8")
            with patch.object(engine, "OUTPUT_DIR", out), patch.object(engine, "download_prices") as download:
                with self.assertRaisesRegex(ValueError, "already frozen"):
                    engine.build(run_id="frozen-run")
                download.assert_not_called()
            self.assertEqual(frozen.read_text(encoding="utf-8"), '{"original":true}')

    def test_duplicate_prediction_does_not_replace_payload(self):
        with tempfile.TemporaryDirectory(dir=engine.OUTPUT_DIR) as folder:
            with patch.object(engine, "STATE_DIR", Path(folder)):
                db = engine.ensure_ledger()
                row = ("r:MU:5", "r", "MU", 5, "2026-09-24", '{"p_bottom":0.2}')
                db.execute("INSERT INTO predictions VALUES (?,?,?,?,?,?)", row)
                db.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO predictions VALUES (?,?,?,?,?,?)", (*row[:-1], '{"p_bottom":0.9}'))
                self.assertEqual(db.execute("SELECT payload_json FROM predictions").fetchone()[0], row[-1])
                db.close()


if __name__ == "__main__":
    unittest.main()
