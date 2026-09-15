"""Real-data build and inference engine for the US turning-point radar.

The engine is deliberately deterministic for a given downloaded snapshot. It
uses Yahoo Finance daily OHLCV as the public-data adapter, a time-ordered
multinomial model for the B3 champion, and a single historical-path scenario
set for probabilities, bands, risk and net-return estimates. The storage
features are calculated as a challenger/shadow layer until their point-in-
time taxonomy has enough out-of-sample history to be calibrated.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.preprocessing import StandardScaler
from sklearn.isotonic import IsotonicRegression

ROOT = Path(__file__).resolve().parents[1]
SEED_CSV = ROOT / "assets" / "universe_seed.csv"
TAXONOMY_JSON = ROOT / "assets" / "research_taxonomy.json"
RAW_DIR = ROOT / "data" / "raw_prices"
OUTPUT_DIR = ROOT / "outputs"
STATE_DIR = ROOT / "state"
SITE_DIR = ROOT / "site"
TAXONOMY_VERSION = "research-taxonomy-20260915-v1.1"
FEATURE_VERSION = "b3-point-in-time-v1"
CHALLENGER_FEATURE_VERSION = "storage-shadow-loo-v1"
MODEL_VERSION = "champion-b3-calibrated-low-confidence-v1"
STRATEGY_VERSION = "next-open-atr-1x-cost-15bp-v1"
SOURCE_NAME = "Yahoo Finance chart OHLCV via yfinance; SEC company_tickers.json for issuer identity"
HORIZONS = (5, 10, 21)
MARKET_SYMBOLS = ("SPY", "QQQ")
COMPARISON_GROUPS = {
    "非存储半导体": "半导体与设备",
    "软件": "软件与网络安全",
    "算力网络与光通信": "算力网络与光通信",
}
BASE_FEATURES = [
    "ret_1", "ret_5", "ret_10", "ret_20", "ret_60",
    "vol_20", "drawdown_20", "dist_ma20", "dist_ma50", "atr_pct",
    "market_ret_5", "market_ret_20", "qqq_rs_5", "group_ret_5",
    "group_ret_20", "group_residual_5", "group_residual_20",
]
TECHNICAL_FEATURES = BASE_FEATURES[:10]
STORAGE_FEATURES = BASE_FEATURES + [
    "storage_loo_ret_5", "storage_loo_ret_20", "subgroup_ret_5",
    "subgroup_ret_20", "storage_breadth_5",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def clean_num(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    ok = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not ok.any():
        return float("nan")
    values, weights = values[ok], weights[ok]
    order = np.argsort(values, kind="mergesort")
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    cutoff = q * cumulative[-1]
    return float(values[min(np.searchsorted(cumulative, cutoff, side="left"), len(values) - 1)])


def quantile_band(values: np.ndarray, weights: np.ndarray) -> list[float] | None:
    lo, hi = weighted_quantile(values, weights, 0.25), weighted_quantile(values, weights, 0.75)
    if not (math.isfinite(lo) and math.isfinite(hi) and lo > 0 and hi >= lo):
        return None
    return [round(lo, 4), round(hi, 4)]


def safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return safe_json(value.item())
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def read_seed() -> list[dict[str, Any]]:
    with SEED_CSV.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        for key in ("business_tags", "industry_tags"):
            row[key] = [x for x in row.get(key, "").split("|") if x]
    return rows


def fetch_sec_identities(symbols: Iterable[str]) -> dict[str, dict[str, Any]]:
    requested = {s.upper().replace(".", "-") for s in symbols}
    result: dict[str, dict[str, Any]] = {}
    try:
        response = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": "us-equity-turning-point-radar/1.1 research contact"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        for row in payload.values():
            ticker = str(row.get("ticker", "")).upper().replace(".", "-")
            if ticker in requested:
                cik = str(row.get("cik_str", "")).zfill(10)
                result[ticker] = {"issuer_id": cik, "issuer_name": row.get("title"), "source": "SEC"}
    except Exception as exc:  # public identity endpoint can rate-limit independently of price data
        result["_error"] = {"message": f"SEC identity lookup unavailable: {type(exc).__name__}: {exc}"}
        # Read-only fallback to the dated SEC exchange master already captured
        # by the existing US market project. This is an identity source only;
        # it is not reused as a price, popularity or forecast signal.
        fallback = Path(r"D:\codex\us-share-daily-market-html\outputs\us_share_technical_screener\2026-09-09\universe_audit\sec_tickers_exchange.json")
        try:
            payload = json.loads(fallback.read_text(encoding="utf-8"))
            fields, rows = payload.get("fields", []), payload.get("data", [])
            idx = {name: i for i, name in enumerate(fields)}
            for row in rows:
                ticker = str(row[idx["ticker"]]).upper().replace(".", "-")
                if ticker in requested:
                    result[ticker] = {"issuer_id": str(row[idx["cik"]]).zfill(10), "issuer_name": row[idx["name"]], "source": "SEC-dated-local-fallback-20260909"}
        except Exception as fallback_exc:
            result["_fallback_error"] = {"message": f"SEC local fallback unavailable: {type(fallback_exc).__name__}: {fallback_exc}"}
    for symbol in requested:
        result.setdefault(symbol, {"issuer_id": None, "issuer_name": None, "source": "unverified"})
    return result


def flatten_download(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if symbol in raw.columns.get_level_values(-1):
            frame = raw.xs(symbol, axis=1, level=-1, drop_level=True)
        elif symbol in raw.columns.get_level_values(0):
            frame = raw.xs(symbol, axis=1, level=0, drop_level=True)
        else:
            return pd.DataFrame()
    else:
        frame = raw.copy()
    rename = {c: str(c).lower().replace(" ", "_") for c in frame.columns}
    frame = frame.rename(columns=rename)
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    if "adj_close" not in frame.columns:
        frame["adj_close"] = frame["close"]
    frame = frame[["open", "high", "low", "close", "adj_close", "volume"]].copy()
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame.apply(pd.to_numeric, errors="coerce").dropna(subset=["close", "high", "low"])
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame.index.name = "date"
    return frame


def download_prices(symbols: list[str], refresh: bool = False) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    frames: dict[str, pd.DataFrame] = {}
    source_errors: dict[str, str] = {}
    utc_date = pd.Timestamp.now(tz="UTC")
    start = (utc_date - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    end = (utc_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    missing = []
    for symbol in symbols:
        cache = RAW_DIR / f"{symbol}.csv"
        frame = pd.DataFrame()
        cached_frame = pd.DataFrame()
        if cache.exists():
            try:
                cached_frame = pd.read_csv(cache, parse_dates=["date"], index_col="date")
            except Exception:
                cached_frame = pd.DataFrame()
        if not refresh:
            frame = cached_frame
        if frame.empty or len(frame) < 80:
            try:
                raw = yf.download(symbol, start=start, end=end, auto_adjust=False, progress=False, threads=False)
                frame = flatten_download(raw, symbol)
                if not frame.empty:
                    frame.to_csv(cache, date_format="%Y-%m-%d")
            except Exception as exc:
                source_errors[symbol] = f"{type(exc).__name__}: {exc}"
                # A refresh must not destroy a previously verified price history just
                # because the public endpoint transiently rate-limited this request.
                # The cached history remains explicitly traceable in the manifest.
                if not cached_frame.empty and len(cached_frame) >= 80:
                    frame = cached_frame
                    source_errors[symbol] += "; using previously cached verified history"
            if (frame.empty or len(frame) < 80) and not cached_frame.empty and len(cached_frame) >= 80:
                frame = cached_frame
                source_errors[symbol] = "refresh returned no rows; using previously cached verified history"
        if frame.empty or len(frame) < 80:
            missing.append(symbol)
        else:
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
            frames[symbol] = frame.sort_index()
    dates = sorted(set.intersection(*(set(f.index) for f in frames.values()))) if frames else []
    # Yahoo can expose a partial bar for the current New York date before the
    # regular session closes.  Never promote that partial bar to a daily radar
    # snapshot; the scheduled runner runs after the close and may include it.
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    cutoff_date = now_ny.date() if now_ny.time() >= time(16, 15) else now_ny.date() - timedelta(days=1)
    complete_dates = [d for d in dates if d.date() <= cutoff_date]
    latest = max(complete_dates).strftime("%Y-%m-%d") if complete_dates else None
    return frames, {
        "source": SOURCE_NAME,
        "requested_symbols": len(symbols),
        "usable_symbols": len(frames),
        "missing_symbols": missing,
        "errors": source_errors,
        "common_latest_date": latest,
        "complete_session_cutoff_ny": cutoff_date.isoformat(),
        "start_requested": start,
        "end_requested": end,
        "retrieved_at": iso(utc_now()),
    }


def pct_rank(values: dict[str, float | None], reverse: bool = False) -> dict[str, float | None]:
    valid = [(k, v) for k, v in values.items() if v is not None and math.isfinite(v)]
    if not valid:
        return {k: None for k in values}
    valid.sort(key=lambda kv: (kv[1], kv[0]), reverse=reverse)
    n = len(valid)
    out = {k: None for k in values}
    for i, (key, _) in enumerate(valid):
        out[key] = round(100 * (n - 1 - i if reverse else i) / max(n - 1, 1), 2)
    return out


def add_features(frame: pd.DataFrame, market: pd.DataFrame, group: pd.Series, subgroup: pd.Series | None) -> pd.DataFrame:
    x = frame.copy()
    close, high, low = x["adj_close"], x["high"], x["low"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14, min_periods=10).mean()
    for n in (1, 5, 10, 20, 60):
        x[f"ret_{n}"] = close.pct_change(n)
    x["vol_20"] = x["ret_1"].rolling(20, min_periods=15).std() * math.sqrt(252)
    x["drawdown_20"] = close / close.rolling(20, min_periods=15).max() - 1
    x["dist_ma20"] = close / close.rolling(20, min_periods=15).mean() - 1
    x["dist_ma50"] = close / close.rolling(50, min_periods=30).mean() - 1
    x["atr_pct"] = x["atr"] / close
    x["market_ret_5"] = market.pct_change(5)
    x["market_ret_20"] = market.pct_change(20)
    x["qqq_rs_5"] = 0.0  # populated by caller when QQQ is available
    x["group_ret_5"] = group.reindex(x.index).pct_change(5)
    x["group_ret_20"] = group.reindex(x.index).pct_change(20)
    x["group_residual_5"] = x["ret_5"] - x["group_ret_5"]
    x["group_residual_20"] = x["ret_20"] - x["group_ret_20"]
    if subgroup is not None:
        x["subgroup_ret_5"] = subgroup.reindex(x.index).pct_change(5)
        x["subgroup_ret_20"] = subgroup.reindex(x.index).pct_change(20)
    else:
        x["subgroup_ret_5"], x["subgroup_ret_20"] = np.nan, np.nan
    return x


def build_group_series(frames: dict[str, pd.DataFrame], seeds: list[dict[str, Any]], group_name: str, exclude: str | None = None) -> pd.Series:
    symbols = [r["symbol"] for r in seeds if r.get("research_group") == group_name and r["symbol"] in frames and r["symbol"] != exclude]
    if not symbols:
        return pd.Series(dtype=float)
    normalized = []
    for symbol in symbols:
        s = frames[symbol]["adj_close"].copy()
        normalized.append(s / s.dropna().iloc[0])
    return pd.concat(normalized, axis=1, sort=False).mean(axis=1, skipna=True).dropna()


def build_storage_loo_series(frames: dict[str, pd.DataFrame], seeds: list[dict[str, Any]], target: str, tag: str | None = None) -> tuple[pd.Series, dict[str, Any]]:
    storage = [r for r in seeds if r.get("research_group_id") == "storage-memory" and r["symbol"] in frames]
    if tag:
        storage = [r for r in storage if tag in r.get("business_tags", [])]
    symbols = [r["symbol"] for r in storage if r["symbol"] != target]
    if not symbols:
        return pd.Series(dtype=float), {"status": "no_peers", "peer_symbols": [], "peer_count": 0, "effective_n": None, "weight_coverage": None, "self_excluded": True}
    series = pd.concat([frames[s]["adj_close"].pct_change().rename(s) for s in symbols], axis=1)
    returns = series.mean(axis=1, skipna=True)
    coverage = series.notna().mean(axis=1)
    status = "validated" if len(symbols) >= 3 else "proxy_only"
    return returns, {
        "status": status,
        "peer_symbols": symbols,
        "peer_count": len(symbols),
        "effective_n": float(len(symbols)),
        "weight_coverage": float(coverage.iloc[-1]) if len(coverage) else None,
        "self_excluded": True,
    }


def label_paths(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    x = frame.copy()
    labels = [None] * len(x)
    ambiguous = [False] * len(x)
    closes, highs, lows, atrs = x["adj_close"].to_numpy(), x["high"].to_numpy(), x["low"].to_numpy(), x["atr"].to_numpy()
    for i in range(len(x)):
        if i + horizon >= len(x) or not np.isfinite(atrs[i]) or atrs[i] <= 0 or not np.isfinite(closes[i]):
            continue
        up, down = closes[i] + atrs[i], max(0.01, closes[i] - atrs[i])
        up_idx = down_idx = None
        for j in range(i + 1, min(len(x), i + horizon + 1)):
            hit_up, hit_down = highs[j] >= up, lows[j] <= down
            if hit_up and hit_down:
                ambiguous[i] = True
                break
            if hit_up and up_idx is None: up_idx = j
            if hit_down and down_idx is None: down_idx = j
            if up_idx is not None or down_idx is not None:
                break
        else:
            labels[i] = "unhit"
            continue
        if ambiguous[i]:
            continue
        if up_idx is not None and (down_idx is None or up_idx < down_idx): labels[i] = "upfirst"
        elif down_idx is not None and (up_idx is None or down_idx < up_idx): labels[i] = "downfirst"
    out = x.copy()
    out["label"] = labels
    out["ambiguous"] = ambiguous
    return out


def future_path(frame: pd.DataFrame, i: int, horizon: int) -> dict[str, Any] | None:
    if i + horizon >= len(frame):
        return None
    row = frame.iloc[i]
    atr = clean_num(row.get("atr")); ref = clean_num(row.get("adj_close"))
    if not atr or not ref or atr <= 0 or ref <= 0:
        return None
    future = frame.iloc[i + 1:i + horizon + 1]
    up_level, down_level = ref + atr, max(0.01, ref - atr)
    label = "unhit"
    ambiguous = False
    for _, bar in future.iterrows():
        up = float(bar["high"]) >= up_level
        down = float(bar["low"]) <= down_level
        if up and down:
            ambiguous = True; label = None; break
        if up:
            label = "upfirst"; break
        if down:
            label = "downfirst"; break
    lows = future["low"].to_numpy(dtype=float)
    highs = future["high"].to_numpy(dtype=float)
    closes = future["adj_close"].to_numpy(dtype=float)
    min_idx = int(np.nanargmin(lows)) if len(lows) else 0
    max_idx = int(np.nanargmax(highs)) if len(highs) else 0
    return {
        "label": label, "ambiguous": ambiguous, "terminal_return": float(closes[-1] / ref - 1),
        "min_drawdown": float(max(0.0, 1 - np.nanmin(lows) / ref)),
        "min_price": float(np.nanmin(lows)), "max_price": float(np.nanmax(highs)),
        "bottom_event": bool(np.nanmin(lows) <= ref - 0.75 * atr and np.nanmax(closes[min_idx:]) >= np.nanmin(lows) + atr),
        "top_event": bool(np.nanmax(highs) >= ref + 0.75 * atr and np.nanmin(closes[max_idx:]) <= np.nanmax(highs) - atr),
    }


def path_summary_frame(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Create leakage-safe historical path summaries with NumPy slices.

    This replaces repeated pandas row iteration during a 100-name build while
    retaining the same conservative ambiguous-bar handling.
    """
    n = len(frame)
    close = frame["adj_close"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    out = {k: [np.nan] * n for k in ("terminal_return", "min_drawdown", "min_price", "max_price")}
    out["bottom_event"], out["top_event"] = [False] * n, [False] * n
    for i in range(max(0, n - horizon)):
        if not (np.isfinite(close[i]) and np.isfinite(atr[i]) and atr[i] > 0):
            continue
        lo, hi, cl = low[i + 1:i + horizon + 1], high[i + 1:i + horizon + 1], close[i + 1:i + horizon + 1]
        if len(cl) < horizon or not np.isfinite(cl).all():
            continue
        up, down = close[i] + atr[i], max(0.01, close[i] - atr[i])
        hit_up, hit_down = hi >= up, lo <= down
        first_up = int(np.flatnonzero(hit_up)[0]) if hit_up.any() else None
        first_down = int(np.flatnonzero(hit_down)[0]) if hit_down.any() else None
        if first_up is not None and first_down is not None and first_up == first_down:
            continue
        min_price, max_price = float(np.nanmin(lo)), float(np.nanmax(hi))
        min_idx, max_idx = int(np.nanargmin(lo)), int(np.nanargmax(hi))
        out["terminal_return"][i] = float(cl[-1] / close[i] - 1)
        out["min_price"][i], out["max_price"][i] = min_price, max_price
        out["min_drawdown"][i] = float(max(0, 1 - min_price / close[i]))
        out["bottom_event"][i] = bool(min_price <= close[i] - 0.75 * atr[i] and np.nanmax(cl[min_idx:]) >= min_price + atr[i])
        out["top_event"][i] = bool(max_price >= close[i] + 0.75 * atr[i] and np.nanmin(cl[max_idx:]) <= max_price - atr[i])
    return pd.DataFrame(out, index=frame.index)


@dataclass
class ModelBundle:
    scaler: StandardScaler
    model: LogisticRegression
    calibrators: dict[str, IsotonicRegression]
    features: list[str]
    medians: pd.Series
    test_metrics: dict[str, Any]

    def predict(self, row: pd.Series) -> np.ndarray:
        values = pd.DataFrame([row.reindex(self.features).fillna(self.medians)]).astype(float)
        raw = self.model.predict_proba(self.scaler.transform(values))[0]
        calibrated = np.array([self.calibrators[c].predict([raw[i]])[0] for i, c in enumerate(self.model.classes_)])
        total = calibrated.sum()
        return calibrated / total if total > 0 else raw / raw.sum()


def train_bundle(samples: pd.DataFrame, features: list[str], horizon: int) -> ModelBundle | None:
    valid = samples.dropna(subset=["label"]).copy()
    valid = valid[valid[features].notna().sum(axis=1) >= max(5, int(len(features) * 0.65))]
    if valid.empty or valid["label"].nunique() < 3:
        return None
    dates = pd.to_datetime(valid["date"])
    max_date = dates.max()
    test_start = max_date - pd.DateOffset(months=6)
    cal_start = max_date - pd.DateOffset(months=12)
    train = valid[dates < cal_start]
    cal = valid[(dates >= cal_start) & (dates < test_start)]
    test = valid[dates >= test_start]
    if len(train) < 300 or len(cal) < 100 or len(test) < 100:
        # Keep the time order but mark the eventual output low confidence.
        cut = int(len(valid) * 0.7); cal_cut = int(len(valid) * 0.85)
        train, cal, test = valid.iloc[:cut], valid.iloc[cut:cal_cut], valid.iloc[cal_cut:]
    if train["label"].nunique() < 3 or len(cal) < 20:
        return None
    medians = train[features].median(numeric_only=True).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    scaler = StandardScaler().fit(train[features].fillna(medians))
    # Current scikit-learn defaults to multinomial behaviour for lbfgs on the
    # multiclass case; avoid the removed multi_class keyword for newer builds.
    model = LogisticRegression(max_iter=700, solver="lbfgs", class_weight="balanced", random_state=7)
    model.fit(scaler.transform(train[features].fillna(medians)), train["label"])
    cal_raw = model.predict_proba(scaler.transform(cal[features].fillna(medians)))
    calibrators = {}
    for i, cls in enumerate(model.classes_):
        ir = IsotonicRegression(out_of_bounds="clip")
        ir.fit(cal_raw[:, i], (cal["label"].to_numpy() == cls).astype(float))
        calibrators[str(cls)] = ir
    test_metrics: dict[str, Any] = {"horizon": horizon, "train_n": len(train), "calibration_n": len(cal), "test_n": len(test), "ambiguous_excluded": int(samples["ambiguous"].sum())}
    if len(test):
        test_raw = model.predict_proba(scaler.transform(test[features].fillna(medians)))
        test_cal = np.column_stack([calibrators[str(cls)].predict(test_raw[:, i]) for i, cls in enumerate(model.classes_)])
        test_cal = test_cal / np.maximum(test_cal.sum(axis=1, keepdims=True), 1e-9)
        y = pd.Categorical(test["label"], categories=model.classes_).codes
        one_hot = np.eye(len(model.classes_))[y]
        test_metrics.update({
            "brier_multiclass": float(np.mean(np.sum((test_cal - one_hot) ** 2, axis=1))),
            "log_loss": float(log_loss(test["label"], test_cal, labels=list(model.classes_))),
            "test_start": str(pd.to_datetime(test["date"]).min().date()),
            "test_end": str(pd.to_datetime(test["date"]).max().date()),
        })
    return ModelBundle(scaler, model, calibrators, features, medians, test_metrics)


def make_samples(frames: dict[str, pd.DataFrame], seeds: list[dict[str, Any]], market: pd.DataFrame, group_series: dict[str, pd.Series], storage_features: bool = False) -> tuple[dict[int, pd.DataFrame], dict[str, pd.DataFrame]]:
    all_samples = {h: [] for h in HORIZONS}
    feature_frames: dict[str, pd.DataFrame] = {}
    storage_members = {r["symbol"] for r in seeds if r.get("research_group_id") == "storage-memory"}
    for row in seeds:
        symbol = row["symbol"]
        if symbol not in frames:
            continue
        group = group_series.get(row.get("research_group", ""), pd.Series(dtype=float))
        subgroup = build_group_series(frames, seeds, row.get("research_group", ""))
        f = add_features(frames[symbol], market, group, subgroup)
        f["qqq_rs_5"] = f["ret_5"] - market.pct_change(5)
        if storage_features and symbol in storage_members:
            loo, _ = build_storage_loo_series(frames, seeds, symbol)
            f["storage_loo_ret_5"] = loo.reindex(f.index).rolling(5, min_periods=3).sum()
            f["storage_loo_ret_20"] = loo.reindex(f.index).rolling(20, min_periods=10).sum()
            tags = row.get("business_tags", [])
            tag = "HDD" if "HDD" in tags else ("NAND" if "NAND" in tags else ("DRAM" if "DRAM" in tags else None))
            sub = build_group_series(frames, seeds, row.get("research_group", "")) if tag is None else pd.concat([frames[r["symbol"]]["adj_close"] for r in seeds if tag in r.get("business_tags", []) and r["symbol"] in frames], axis=1).mean(axis=1)
            f["subgroup_ret_5"] = sub.reindex(f.index).pct_change(5)
            f["subgroup_ret_20"] = sub.reindex(f.index).pct_change(20)
            f["storage_breadth_5"] = float(len(storage_members))
        for h in HORIZONS:
            labeled = label_paths(f, h)
            labeled = labeled.join(path_summary_frame(f, h))
            labeled["symbol"], labeled["date"] = symbol, labeled.index
            sample_columns = list(dict.fromkeys(BASE_FEATURES + ["adj_close", "atr", "label", "ambiguous", "symbol", "date", "terminal_return", "min_drawdown", "min_price", "max_price", "bottom_event", "top_event"]))
            all_samples[h].append(labeled[sample_columns].copy())
        feature_frames[symbol] = f
    return {h: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for h, v in all_samples.items()}, feature_frames


def build_scenario_metric(symbol: str, current: pd.Series, historical: pd.DataFrame, bundle: ModelBundle | None, horizon: int, current_frame: pd.DataFrame) -> dict[str, Any]:
    ref, atr = clean_num(current.get("adj_close")), clean_num(current.get("atr"))
    if not ref or not atr or atr <= 0:
        return {"status": "data_error"}
    features = bundle.features if bundle else BASE_FEATURES
    candidate = historical.copy()
    candidate = candidate[candidate["date"] < current.name]
    candidate = candidate[candidate["label"].notna() & (candidate["ambiguous"] == False)]
    candidate = candidate.dropna(subset=["adj_close", "atr"])
    path_columns = ["terminal_return", "min_drawdown", "min_price", "max_price", "bottom_event", "top_event"]
    candidate = candidate.dropna(subset=path_columns)
    if candidate.empty:
        return {"status": "structural_only"}
    cur_vec = current.reindex(features).astype(float)
    med = candidate[features].median(numeric_only=True).fillna(0.0)
    mat = candidate[features].fillna(med).to_numpy(dtype=float)
    vec = cur_vec.fillna(med).to_numpy(dtype=float)
    scale = np.nanstd(mat, axis=0); scale[~np.isfinite(scale) | (scale == 0)] = 1.0
    dist = np.sqrt(np.nanmean(((mat - vec) / scale) ** 2, axis=1))
    candidate = candidate.assign(_distance=dist).sort_values(["_distance", "date"], kind="mergesort").head(160)
    if bundle:
        model_p = dict(zip(bundle.model.classes_, bundle.predict(current)))
    else:
        model_p = {"upfirst": 1 / 3, "downfirst": 1 / 3, "unhit": 1 / 3}
    empirical = candidate["label"].value_counts(normalize=True)
    records = []
    for _, row in candidate.iterrows():
        # A compact stored path uses normalized future statistics produced during sample construction.
        if not all(k in row for k in ["terminal_return", "min_drawdown", "min_price", "max_price", "bottom_event", "top_event"]):
            continue
        cls = str(row["label"])
        prior = max(float(empirical.get(cls, 0.05)), 0.05)
        target = max(float(model_p.get(cls, 1 / 3)), 0.01)
        weight = math.exp(-float(row["_distance"])) * min(4.0, target / prior)
        scale_ratio = atr / max(float(row["atr"]), 1e-6)
        terminal = ref * (1 + float(row["terminal_return"]) * min(2.0, max(0.5, scale_ratio)))
        min_price = ref - (ref - float(row["min_price"])) * min(2.0, max(0.5, scale_ratio))
        max_price = ref + (float(row["max_price"]) - float(row["adj_close"])) * min(2.0, max(0.5, scale_ratio))
        dd = max(0.0, 1 - min_price / ref)
        records.append({"label": cls, "weight": weight, "terminal": terminal, "min_price": min_price, "max_price": max_price, "drawdown": dd, "bottom": bool(row["bottom_event"]), "top": bool(row["top_event"])})
    if not records:
        return {"status": "structural_only"}
    weights = np.array([r["weight"] for r in records], dtype=float); weights /= weights.sum()
    terminal = np.array([r["terminal"] for r in records]); mins = np.array([r["min_price"] for r in records]); maxs = np.array([r["max_price"] for r in records]); dds = np.array([r["drawdown"] for r in records])
    net = terminal / ref - 1 - 0.0015
    expected = float(np.sum(weights * net)); sd = float(np.sqrt(np.sum(weights * (net - expected) ** 2)))
    eff_n = float(1 / np.sum(weights ** 2)); mu_lcb = expected - 1.645 * sd / math.sqrt(max(eff_n, 1))
    risk = float(np.mean(np.sort(dds)[-max(1, int(math.ceil(len(dds) * 0.05))):]))
    opportunity = mu_lcb / max(risk, 0.02)
    bottom_mask = np.array([r["bottom"] for r in records], dtype=bool); top_mask = np.array([r["top"] for r in records], dtype=bool)
    def event_prob(mask: np.ndarray) -> float: return float(np.sum(weights[mask])) if mask.any() else 0.0
    return {
        "status": "calibrated_low_confidence", "opportunity_value": clean_num(opportunity), "risk_value": risk,
        "expected_return": expected, "es95": risk, "mu_lcb": mu_lcb, "effective_scenario_n": eff_n,
        "p_upfirst": float(np.sum(weights * np.array([r["label"] == "upfirst" for r in records]))),
        "p_downfirst": float(np.sum(weights * np.array([r["label"] == "downfirst" for r in records]))),
        "p_unhit": float(np.sum(weights * np.array([r["label"] == "unhit" for r in records]))),
        "p_bottom": event_prob(bottom_mask), "p_top": event_prob(top_mask),
        "bottom_band": quantile_band(mins[bottom_mask], weights[bottom_mask]) if bottom_mask.any() else quantile_band(mins, weights),
        "top_band": quantile_band(maxs[top_mask], weights[top_mask]) if top_mask.any() else quantile_band(maxs, weights),
        "terminal_band": quantile_band(terminal, weights),
        "terminal_p10": weighted_quantile(terminal, weights, 0.1), "terminal_p50": weighted_quantile(terminal, weights, 0.5), "terminal_p90": weighted_quantile(terminal, weights, 0.9),
        "scenario_set": "nearest-point-in-time-history-with-model-class-reweighting",
        "scenario_count": len(records), "cost_assumption": 0.0015,
    }


def storage_rotation(frames: dict[str, pd.DataFrame], seeds: list[dict[str, Any]], as_of: pd.Timestamp) -> dict[str, Any]:
    storage = build_group_series(frames, seeds, "存储与内存")
    market = frames["SPY"]["adj_close"] if "SPY" in frames else pd.Series(dtype=float)
    pairs = [("市场基准", market), ("非存储半导体", build_group_series(frames, seeds, "半导体与设备")), ("软件", build_group_series(frames, seeds, "软件与网络安全")), ("算力网络与光通信", build_group_series(frames, seeds, "算力网络与光通信"))]
    comparisons = []
    for label, base in pairs:
        for window in (1, 3, 5, 10, 20):
            if storage.empty or base.empty: rel = None; status = "unavailable"
            else:
                s = storage.reindex([as_of - pd.tseries.offsets.BDay(window), as_of]).dropna()
                b = base.reindex(s.index).dropna()
                rel = float((s.iloc[-1] / s.iloc[0] - 1) - (b.iloc[-1] / b.iloc[0] - 1)) if len(s) == 2 and len(b) == 2 else None
                status = "observed" if rel is not None else "unavailable"
            comparisons.append({"label": f"存储相对{label}", "window_sessions": window, "relative_return": rel, "status": status, "note": "同一as_of；不构成板块共振概率。"})
    summary_vals = [c for c in comparisons if c["window_sessions"] in (5, 20) and c["relative_return"] is not None]
    summary = "；".join(f"{c['label']} {c['window_sessions']}日 {c['relative_return'] * 100:+.1f}%" for c in summary_vals[:4]) if summary_vals else "暂无同一时点的存储轮动收益数据；不编造走势。"
    subgroup = {}
    for name, tags in (("DRAM/HBM", ("DRAM", "HBM")), ("NAND/SSD", ("NAND", "SSD")), ("HDD", ("HDD",))):
        members = [r["symbol"] for r in seeds if any(t in r.get("business_tags", []) for t in tags) and r["symbol"] in frames]
        subgroup[name] = {"members": members, "overlap_issuer_ids": ["MU"] if name in ("DRAM/HBM", "NAND/SSD") else [], "note": "MU在DRAM/HBM与NAND/SSD两个视图重叠，不能相加。" if name in ("DRAM/HBM", "NAND/SSD") else "HDD视图按当前研究标签展示。"}
    return {"status": "observed" if comparisons else "unavailable", "as_of": as_of.strftime("%Y-%m-%d"), "summary": summary, "comparisons": comparisons, "subgroups": subgroup, "parent_snapshot_id": "yfinance-daily-ohlcv", "taxonomy_version": TAXONOMY_VERSION, "membership_version": "seed-membership-20260915-v1.1", "feature_version": CHALLENGER_FEATURE_VERSION, "method": "derived-in-new-radar-adapter-read-only-source"}


def enrich_record(row: dict[str, Any], frame: pd.DataFrame, metric_by_h: dict[int, dict[str, Any]], identity: dict[str, Any], peers: dict[str, Any], as_of: pd.Timestamp) -> dict[str, Any]:
    current = frame.iloc[-1]
    metrics = {str(h): metric_by_h.get(h, {"status": "data_error"}) for h in HORIZONS}
    stage = "整理"
    if clean_num(current.get("ret_5")) is not None and current["ret_5"] < -0.05 and current.get("ret_1", 0) > 0:
        stage = "下跌后修复观察"
    elif current.get("dist_ma20", 0) > 0 and current.get("ret_20", 0) > 0:
        stage = "趋势延续观察"
    elif current.get("dist_ma20", 0) < 0 and current.get("ret_20", 0) < 0:
        stage = "回撤/风险观察"
    storage = row.get("research_group_id") == "storage-memory"
    peer_note = peers.get("note", "")
    if storage:
        peer_note = (peer_note + " " if peer_note else "") + "存储因子本轮仅影子运行；分类与轮动已接入，未把新特征套用到旧校准器。"
    return {**row, "as_of": as_of.strftime("%Y-%m-%d"), "reference_price": clean_num(current["adj_close"]), "metrics": safe_json(metrics), "stage": stage, "issuer_id": identity.get("issuer_id"), "issuer_identity_source": identity.get("source"), "peer_context": safe_json({**peers, "note": peer_note, "issuer_id": identity.get("issuer_id"), "subgroup_context": peers.get("subgroup_context", {})}), "rotation_explanation": "真实日线数据已接入；市场、研究组与个股残差分别计算，允许不同步。" + (" 存储细分与LOO为影子候选，未进入正式校准分数。" if storage else ""), "trigger_summary": "确认：收盘越过基于当日ATR的参考区域并保持；失效：跳空、事件冲击或重新跌破结构。具体价带与路径仅在对应周期有数据时展示。", "event_summary": "本次生产构建未抓取并公开长文本事件正文；事件特征为缺失，不把标题或业务分类当作催化概率。", "risk_summary": "风险值来自共同历史路径的最差5%不利幅度均值，未假设保护价一定成交；执行成本按策略版本扣除。", "data_note": f"数据源：{SOURCE_NAME}；截止{as_of.strftime('%Y-%m-%d')}。B3已按时间切分并独立校准；存储新特征为挑战者影子状态。"}


def ensure_ledger() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE_DIR / "radar.sqlite3")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, as_of TEXT, model_version TEXT, feature_version TEXT, status TEXT, manifest_path TEXT);
    CREATE TABLE IF NOT EXISTS predictions (prediction_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, symbol TEXT NOT NULL, horizon INTEGER NOT NULL, as_of TEXT, payload_json TEXT NOT NULL, UNIQUE(run_id,symbol,horizon));
    CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, client_request_id TEXT, symbol TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, run_id TEXT, result_json TEXT, error_json TEXT);
    """)
    db.commit()
    return db


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe_json(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def build(refresh: bool = False, run_id: str | None = None, extra_symbol: str | None = None) -> dict[str, Any]:
    print("[radar] loading seed and price snapshot", flush=True)
    seeds = read_seed()
    seed_symbols = [r["symbol"] for r in seeds]
    extra_symbol = extra_symbol.upper().replace(".", "-") if extra_symbol else None
    symbols = list(dict.fromkeys(seed_symbols + ([extra_symbol] if extra_symbol else []) + list(MARKET_SYMBOLS)))
    frames, source = download_prices(symbols, refresh=refresh)
    print(f"[radar] prices ready: {len(frames)}/{len(symbols)}", flush=True)
    if "SPY" not in frames:
        raise RuntimeError("SPY market benchmark unavailable; cannot build a live radar")
    as_of = max(frames["SPY"].index)
    sec = fetch_sec_identities(seed_symbols)
    groups = {r.get("research_group", "") for r in seeds}
    group_series = {g: build_group_series(frames, seeds, g) for g in groups}
    market = frames["SPY"]["adj_close"]
    samples, feature_frames = make_samples(frames, seeds, market, group_series, storage_features=False)
    print("[radar] point-in-time labels ready", flush=True)
    bundles: dict[int, ModelBundle | None] = {}
    backtest: dict[str, Any] = {"model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION, "strategy_version": STRATEGY_VERSION, "horizons": {}, "storage_incremental": {"status": "BLOCKED", "reason": "taxonomy effective_from=2026-09-15 leaves no mature point-in-time storage membership window for a same-window sample-out test; challenger is shadow-only."}}
    for h in HORIZONS:
        b2 = train_bundle(samples[h], TECHNICAL_FEATURES, h)
        bundle = train_bundle(samples[h], BASE_FEATURES, h)
        bundles[h] = bundle
        backtest["horizons"][str(h)] = {"b2_technical": b2.test_metrics if b2 else {"status": "insufficient_training_data"}, "b3_market_rotation": bundle.test_metrics if bundle else {"status": "insufficient_training_data"}}
        print(f"[radar] model {h}d ready", flush=True)
    records = []
    latest_metrics = {h: {} for h in HORIZONS}
    for row in seeds:
        symbol = row["symbol"]
        if symbol not in feature_frames:
            rec = {**row, "as_of": as_of.strftime("%Y-%m-%d"), "reference_price": None, "metrics": {str(h): {"status": "data_error"} for h in HORIZONS}, "data_note": "行情源未返回足够历史，未填价格或概率。"}
            records.append(rec); continue
        frame = feature_frames[symbol]
        current = frame.loc[as_of] if as_of in frame.index else frame.iloc[-1]
        metric_by_h = {}
        for h in HORIZONS:
            hist = samples[h][samples[h]["symbol"] == symbol].copy()
            metric_by_h[h] = build_scenario_metric(symbol, current, hist, bundles[h], h, frame)
            latest_metrics[h][symbol] = metric_by_h[h].get("opportunity_value")
        peers = {"status": "unavailable", "self_excluded": False, "peer_symbols": [], "peer_count": 0, "effective_n": None, "weight_coverage": None, "note": "非存储股票不计算存储同行。"}
        if row.get("research_group_id") == "storage-memory":
            loo, peer_meta = build_storage_loo_series(frames, seeds, symbol)
            identity_ok = bool(sec.get(symbol, {}).get("issuer_id"))
            peers = {**peer_meta, "status": peer_meta.get("status") if identity_ok else "identity_unverified", "self_excluded": identity_ok, "issuer_id": sec.get(symbol, {}).get("issuer_id"), "subgroup_context": {}}
            for tag in ("DRAM", "NAND", "HDD"):
                _, sm = build_storage_loo_series(frames, seeds, symbol, tag=tag)
                peers["subgroup_context"][tag] = sm
        records.append(enrich_record(row, frame, metric_by_h, sec.get(symbol, {}), peers, as_of))
        if len(records) % 10 == 0:
            print(f"[radar] records {len(records)}/100", flush=True)
    for h in HORIZONS:
        opp = pct_rank(latest_metrics[h])
        risk_vals = {s: (clean_num(records[[r["symbol"] for r in records].index(s)]["metrics"].get(str(h), {}).get("risk_value")) if s in [r["symbol"] for r in records] else None) for s in latest_metrics[h]}
        risk = pct_rank(risk_vals)
        for rec in records:
            m = rec["metrics"].get(str(h), {})
            if m.get("status") in ("calibrated", "calibrated_low_confidence"):
                m["opportunity_score"] = opp.get(rec["symbol"])
                m["risk_score"] = risk.get(rec["symbol"])
                m["positive_edge"] = bool((m.get("mu_lcb") or 0) > 0)
    temporary: list[dict[str, Any]] = []
    if extra_symbol and extra_symbol in frames:
        extra_row = {"symbol": extra_symbol, "name_zh": "临时观察", "coverage_bucket": "临时观察", "research_group": "未归类", "research_group_id": "temporary", "legacy_research_group": "", "business_tags": [], "industry_tags": [], "taxonomy_version": TAXONOMY_VERSION, "taxonomy_effective_from": "2026-09-15", "metadata_as_of": as_of.strftime("%Y-%m-%d")}
        raw_extra = add_features(frames[extra_symbol], market, pd.Series(dtype=float), None)
        current_extra = raw_extra.loc[as_of] if as_of in raw_extra.index else raw_extra.iloc[-1]
        extra_metrics: dict[int, dict[str, Any]] = {}
        for h in HORIZONS:
            extra_hist = samples[h].copy()
            extra_metrics[h] = build_scenario_metric(extra_symbol, current_extra, extra_hist, bundles[h], h, raw_extra)
        extra_peers = {"status": "unavailable", "self_excluded": False, "peer_symbols": [], "peer_count": 0, "effective_n": None, "weight_coverage": None, "note": "临时股票未被强行归入存储或其他研究组；结果与常态100池分开保存。"}
        temporary.append(enrich_record(extra_row, raw_extra, extra_metrics, {"issuer_id": sec.get(extra_symbol, {}).get("issuer_id"), "source": sec.get(extra_symbol, {}).get("source", "unverified")}, extra_peers, as_of))
    run_id = run_id or f"radar-{as_of.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    rotation = storage_rotation(frames, seeds, as_of)
    print("[radar] storage rotation ready", flush=True)
    valid = sum(1 for r in records if any(r["metrics"].get(str(h), {}).get("status") in ("calibrated", "calibrated_low_confidence") for h in HORIZONS))
    mother_file = Path(r"D:\codex\us-share-daily-market-html\outputs\us_share_technical_screener\2026-09-13\all_metrics.csv")
    mother_count = 0
    if mother_file.exists():
        with mother_file.open(encoding="utf-8-sig", newline="") as fh:
            mother_count = max(0, sum(1 for _ in fh) - 1)
    source["mother_pool"] = {"source": "us-share-daily-market-html/all_metrics.csv", "available_symbols": mother_count, "qualification_verified": False, "note": "母池规模来自旧美股行情项目快照；本项目未把它改写成当前人气排名。"}
    data = {"build_mode": "live", "status_message": "真实日线行情已接入；概率为按时间切分并独立校准的B3低可信结果。存储分类/轮动已接入，存储因子仍为影子挑战者，未宣称增益。", "generated_at": iso(utc_now()), "as_of": as_of.strftime("%Y-%m-%d"), "as_of_beijing": f"{as_of.strftime('%Y-%m-%d')} 纽约收盘数据；北京时间日期需按交易日换算", "run_id": run_id, "prediction_snapshot_id": run_id, "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION, "strategy_version": STRATEGY_VERSION, "universe_version": "curated-seed-20260915-v1.1-live-validation", "taxonomy_version": TAXONOMY_VERSION, "source_manifest": source, "records": records, "temporary": temporary, "storage_rotation": rotation, "rotation_summary": [rotation["summary"], "存储四只为同一主研究组；细分视图允许MU重叠，主表不重复计数。", "存储新因子为shadow/challenger，未套用旧校准器；不要把MU强弱写成SNDK/WDC/STX的固定结论。"], "public_config": {"api_base_url": None}, "backtest": backtest, "coverage": {"regular_pool": len(records), "valid_forecast_records": valid, "usable_price_records": sum(1 for r in records if r.get("reference_price") is not None), "data_cutoff": as_of.strftime("%Y-%m-%d"), "financial_backtest_status": "B3 time-split metrics computed; storage incremental alpha BLOCKED"}}
    write_json(OUTPUT_DIR / f"dashboard-{run_id}.json", data)
    write_json(OUTPUT_DIR / f"backtest-{run_id}.json", backtest)
    write_json(STATE_DIR / "model_card.json", {"model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION, "calibration": "independent time calibration window", "status": "calibrated_low_confidence", "champion": "B3 without storage challenger", "challenger": "B3 + storage LOO/subgroup factors shadow-only", "backtest": backtest, "source": source})
    db = ensure_ledger()
    db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)", (run_id, iso(utc_now()), data["as_of"], MODEL_VERSION, FEATURE_VERSION, "succeeded", str(OUTPUT_DIR / f"dashboard-{run_id}.json")))
    for rec in records:
        for h in HORIZONS:
            db.execute("INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?)", (f"{run_id}:{rec['symbol']}:{h}", run_id, rec["symbol"], h, rec["as_of"], json.dumps(rec["metrics"][str(h)], ensure_ascii=False),))
    db.commit(); db.close()
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-download public OHLCV")
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path, help="copy live dashboard JSON to this path")
    parser.add_argument("--extra-symbol", help="temporary pool-external symbol, computed by the same engine")
    args = parser.parse_args()
    data = build(refresh=args.refresh, run_id=args.run_id, extra_symbol=args.extra_symbol)
    if args.output:
        write_json(args.output, data)
    print(json.dumps({"run_id": data["run_id"], "as_of": data["as_of"], "pool": len(data["records"]), "valid_forecasts": data["coverage"]["valid_forecast_records"], "storage_factor": data["backtest"]["storage_incremental"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
