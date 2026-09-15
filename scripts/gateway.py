"""Small persistent, authenticated job gateway for local/runner deployments.

It intentionally uses only the Python standard library. Set RADAR_API_TOKEN to
an out-of-band secret before starting. The public Pages build never contains
this token and keeps api_base_url null until an HTTPS deployment is configured.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from radar_engine import ROOT, build, ensure_ledger, write_json
except ModuleNotFoundError:  # package-style import used by tests
    from scripts.radar_engine import ROOT, build, ensure_ledger, write_json

TOKEN = os.environ.get("RADAR_API_TOKEN", "")
HOST = os.environ.get("RADAR_GATEWAY_HOST", "127.0.0.1")
PORT = int(os.environ.get("RADAR_GATEWAY_PORT", "8787"))
SYMBOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def latest_dashboard() -> dict:
    files = sorted((ROOT / "outputs").glob("dashboard-radar-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No live dashboard snapshot exists; run the batch builder first")
    return json.loads(files[0].read_text(encoding="utf-8"))


def set_job(job_id: str, status: str, **fields: object) -> None:
    db = ensure_ledger()
    cols, values = ["status", "updated_at"], [status, now()]
    for key, value in fields.items():
        cols.append(key); values.append(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
    assignments = ",".join(f"{c}=?" for c in cols)
    db.execute(f"UPDATE jobs SET {assignments} WHERE job_id=?", (*values, job_id)); db.commit(); db.close()


def run_job(job_id: str, symbol: str) -> None:
    try:
        set_job(job_id, "running")
        snapshot = latest_dashboard()
        regular = {r["symbol"].upper(): r for r in snapshot.get("records", [])}
        if symbol in regular:
            result = regular[symbol]
            set_job(job_id, "succeeded", run_id=snapshot.get("run_id"), result_json=json.dumps(result, ensure_ascii=False))
            return
        # Pool-external names go through the same build() entry point. The new
        # result is stored as temporary and cannot alter regular-pool ranks.
        result_snapshot = build(extra_symbol=symbol, run_id=f"single-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}")
        result = next((r for r in result_snapshot.get("temporary", []) if r.get("symbol") == symbol), None)
        if not result:
            raise RuntimeError("symbol resolved but no temporary result was produced")
        set_job(job_id, "succeeded", run_id=result_snapshot.get("run_id"), result_json=json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        set_job(job_id, "failed", error_json=json.dumps({"category": "compute_error", "message": str(exc)}, ensure_ascii=False))


class Handler(BaseHTTPRequestHandler):
    server_version = "RadarGateway/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[gateway] {self.address_string()} {fmt % args}")

    def json_response(self, code: int, body: object) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        return bool(TOKEN) and supplied == f"Bearer {TOKEN}"

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16_384:
            raise ValueError("invalid request body size")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self.json_response(200, {"gateway": "ready" if TOKEN else "blocked_missing_RADAR_API_TOKEN", "runner": "local-python", "project": "us-equity-turning-point-radar"}); return
        if not self.authorized(): self.json_response(401, {"error": "unauthorized"}); return
        if path.startswith("/jobs/"):
            job_id = unquote(path.split("/", 2)[2]); db = ensure_ledger(); row = db.execute("SELECT job_id,symbol,status,created_at,updated_at,run_id,result_json,error_json FROM jobs WHERE job_id=?", (job_id,)).fetchone(); db.close()
            if not row: self.json_response(404, {"error": "job_not_found"}); return
            keys = ["job_id", "symbol", "status", "created_at", "updated_at", "run_id", "result_json", "error_json"]; out = dict(zip(keys, row));
            for key in ("result_json", "error_json"):
                if out[key]: out[key] = json.loads(out[key])
            if out.get("result_json"): out["result"] = out.pop("result_json")
            if out.get("error_json"): out["error"] = out.pop("error_json")
            self.json_response(200, out); return
        if path.startswith("/results/"):
            job_id = unquote(path.split("/", 2)[2]); db = ensure_ledger(); row = db.execute("SELECT result_json,status FROM jobs WHERE job_id=?", (job_id,)).fetchone(); db.close()
            if not row or row[0] is None: self.json_response(404, {"error": "result_not_ready"}); return
            self.json_response(200, json.loads(row[0])); return
        self.json_response(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self.authorized(): self.json_response(401, {"error": "unauthorized"}); return
        if urlparse(self.path).path != "/jobs": self.json_response(404, {"error": "not_found"}); return
        try:
            body = self.read_body(); symbol = str(body.get("symbol", "")).strip().upper().replace(".", "-")
            if not SYMBOL_RE.fullmatch(symbol): raise ValueError("symbol must be a simple ticker; ambiguous names require prior resolution")
            client_id = str(body.get("client_request_id", ""))[:100]
            job_id = f"job-{uuid.uuid4().hex}"
            db = ensure_ledger(); db.execute("INSERT INTO jobs(job_id,client_request_id,symbol,status,created_at,updated_at) VALUES (?,?,?,?,?,?)", (job_id, client_id, symbol, "queued", now(), now())); db.commit(); db.close()
            threading.Thread(target=run_job, args=(job_id, symbol), daemon=True).start()
            self.json_response(202, {"job_id": job_id, "status": "queued", "created_at": now()})
        except ValueError as exc: self.json_response(400, {"error": "invalid_request", "message": str(exc)})
        except Exception as exc: self.json_response(500, {"error": "gateway_error", "message": str(exc)})


if __name__ == "__main__":
    print(f"Starting authenticated radar gateway on http://{HOST}:{PORT}; token_configured={bool(TOKEN)}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
