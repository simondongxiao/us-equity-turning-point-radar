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

try:
    from potential_ranges import build_potential_ranges
    from index_context import INDEX_SPECS, build_index_context
except ModuleNotFoundError:
    from scripts.potential_ranges import build_potential_ranges
    from scripts.index_context import INDEX_SPECS, build_index_context

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
MODEL_VERSION = "champion-b3-calibrated-low-confidence-v1.3.0"
DIRECTIONAL_MODEL_VERSION = "challenger-exclusive-directional-turn-v1.3.0"
CONFIDENCE_METHOD_VERSION = "path-validation-confidence-v1.3.0"
STRATEGY_VERSION = "next-open-atr-1x-cost-15bp-decision-audit-v1.3.0"
DECISION_AUDIT_VERSION = "decision-consistency-and-risk-audit-v1.3.0"
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
    refresh_regressions: dict[str, str] = {}
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
                downloaded = flatten_download(raw, symbol)
                if not downloaded.empty:
                    if not cached_frame.empty:
                        cached_frame.index = pd.to_datetime(cached_frame.index).tz_localize(None)
                        cached_latest = cached_frame.index.max()
                        downloaded_latest = downloaded.index.max()
                        if downloaded_latest < cached_latest:
                            refresh_regressions[symbol] = (
                                f"download ended {downloaded_latest.date()} before verified cache {cached_latest.date()}; "
                                "retained newer cached sessions"
                            )
                        # Prefer the fresh payload on overlapping sessions, but never
                        # erase newer verified sessions merely because a public source
                        # returned a temporarily truncated history.
                        frame = pd.concat([cached_frame, downloaded])
                        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
                    else:
                        frame = downloaded
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
        "refresh_regressions": refresh_regressions,
        "common_latest_date": latest,
        "latest_complete_date_by_symbol": {
            symbol: max(d for d in frame.index if d.date() <= cutoff_date).strftime("%Y-%m-%d")
            for symbol, frame in frames.items()
            if any(d.date() <= cutoff_date for d in frame.index)
        },
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


def _trailing_observed_return(series: pd.Series, as_of: pd.Timestamp, window: int) -> float | None:
    """Return the last observed window-session return without assuming a holiday calendar."""
    observed = series.loc[:as_of].dropna()
    if len(observed) <= window:
        return None
    start = clean_num(observed.iloc[-window - 1])
    end = clean_num(observed.iloc[-1])
    if start is None or end is None or start <= 0:
        return None
    return float(end / start - 1.0)


def research_group_rotation(
    frames: dict[str, pd.DataFrame],
    seeds: list[dict[str, Any]],
    as_of: pd.Timestamp,
    windows: tuple[int, ...] = (5, 20),
    minimum_members: int = 2,
) -> dict[str, Any]:
    """Describe all eligible research groups relative to SPY; never alter model scores."""
    benchmark = frames.get("SPY", pd.DataFrame()).get("adj_close", pd.Series(dtype=float))
    benchmark_returns = {str(window): _trailing_observed_return(benchmark, as_of, window) for window in windows}
    group_names = sorted({str(row.get("research_group") or "").strip() for row in seeds if row.get("research_group")})
    groups: list[dict[str, Any]] = []
    for group_name in group_names:
        symbols = sorted({row["symbol"] for row in seeds if row.get("research_group") == group_name and row.get("symbol") in frames})
        returns: dict[str, float | None] = {}
        relative: dict[str, float | None] = {}
        observed_members: dict[str, int] = {}
        for window in windows:
            values = [
                value
                for symbol in symbols
                if (value := _trailing_observed_return(frames[symbol]["adj_close"], as_of, window)) is not None
            ]
            observed_members[str(window)] = len(values)
            group_return = float(np.mean(values)) if len(values) >= minimum_members else None
            returns[str(window)] = group_return
            market_return = benchmark_returns[str(window)]
            relative[str(window)] = float(group_return - market_return) if group_return is not None and market_return is not None else None
        groups.append(
            {
                "research_group": group_name,
                "members": symbols,
                "member_count": len(symbols),
                "minimum_members": minimum_members,
                "observed_members": observed_members,
                "equal_weight_return": returns,
                "relative_to_spy": relative,
            }
        )

    rankings: dict[str, dict[str, list[dict[str, Any]]]] = {}
    summary: list[str] = []
    for window in windows:
        eligible = [
            {"research_group": row["research_group"], "relative_return": row["relative_to_spy"][str(window)]}
            for row in groups
            if row["relative_to_spy"][str(window)] is not None
        ]
        eligible.sort(key=lambda row: (row["relative_return"], row["research_group"]), reverse=True)
        strongest = eligible[:2]
        weakest = sorted(eligible[-2:], key=lambda row: (row["relative_return"], row["research_group"])) if eligible else []
        rankings[str(window)] = {"strongest": strongest, "weakest": weakest}
        if eligible:
            fmt = lambda rows: "、".join(f"{row['research_group']} {row['relative_return'] * 100:+.1f}%" for row in rows)
            summary.append(f"{window}日相对SPY最强：{fmt(strongest)}；最弱：{fmt(weakest)}")
    if not summary:
        summary = ["暂无满足至少2只成分、同一数据时点的研究组轮动数据；不编造强弱结论。"]
    summary.append("研究组按成员个股等权收益计算，仅作轮动描述；不改写个股顶底概率、机会/风险分或决策榜。")
    return {
        "status": "observed" if rankings and any(value["strongest"] for value in rankings.values()) else "unavailable",
        "as_of": as_of.strftime("%Y-%m-%d"),
        "benchmark": "SPY",
        "windows": list(windows),
        "minimum_members": minimum_members,
        "groups": groups,
        "rankings": rankings,
        "summary": summary,
        "method": "equal-weight mean of member trailing observed returns minus SPY over the same session window",
        "note": "全研究组采用同一规则动态排序；存储与内存不享有首页固定席位。",
    }


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


def add_directional_turn_label(samples: pd.DataFrame) -> pd.DataFrame:
    """Add a mutually exclusive first-passage plus reversal target.

    A bottom/top turn is counted only when that side's 1 ATR boundary is the
    first boundary reached and the corresponding 0.75 ATR excursion followed
    by a 1 ATR close reversal is observed inside the same future window.
    Everything else with a mature, unambiguous first-touch label is the
    no-directional-turn class. This deliberately does not force independent
    bottom and top event marginals to sum to one.
    """
    out = samples.copy()
    mature = out["label"].notna() & out["ambiguous"].eq(False)
    out["turn_label"] = None
    out.loc[mature, "turn_label"] = "no_directional_turn"
    out.loc[mature & out["label"].eq("downfirst") & out["bottom_event"].eq(True), "turn_label"] = "bottom_rebound_first"
    out.loc[mature & out["label"].eq("upfirst") & out["top_event"].eq(True), "turn_label"] = "top_reversal_first"
    return out


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


def calibration_diagnostics(probabilities: np.ndarray, truths: Iterable[Any], classes: list[str],
                            dates: Iterable[Any] | None = None, block_ci: bool = False) -> dict[str, Any]:
    """Return auditable multiclass calibration bins and an average classwise ECE.

    The optional confidence interval resamples whole evaluation dates, not
    individual stock rows, so a 100-stock market day is not treated as 100
    independent observations. The curve remains a diagnostic; it is not a
    claim that every probability bin is stable in a new regime.
    """
    probability_array = np.asarray(probabilities, dtype=float)
    truth_array = np.asarray([str(value) for value in truths], dtype=object)
    if probability_array.ndim != 2 or len(probability_array) != len(truth_array) or not len(truth_array):
        return {"calibration_curve": {}, "calibration_ece": None}
    date_array = np.asarray([str(pd.to_datetime(value).date()) for value in dates], dtype=object) if dates is not None else None
    edges = np.linspace(0.0, 1.0, 11)
    curves: dict[str, list[dict[str, Any]]] = {}
    class_eces: list[float] = []
    rng = np.random.default_rng(20260929)
    for class_index, class_name in enumerate(classes):
        predicted = probability_array[:, class_index]
        actual = (truth_array == str(class_name)).astype(float)
        bins: list[dict[str, Any]] = []
        weighted_gap = 0.0
        for bin_index in range(10):
            lower, upper = float(edges[bin_index]), float(edges[bin_index + 1])
            mask = (predicted >= lower) & ((predicted < upper) if bin_index < 9 else (predicted <= upper))
            count = int(mask.sum())
            if not count:
                continue
            predicted_mean = float(predicted[mask].mean())
            observed_rate = float(actual[mask].mean())
            row: dict[str, Any] = {"bin_lower": lower, "bin_upper": upper, "predicted_mean": predicted_mean,
                "observed_rate": observed_rate, "n": count}
            if block_ci and date_array is not None:
                selected_dates = date_array[mask]
                unique_dates = np.unique(selected_dates)
                if len(unique_dates) >= 10:
                    daily_success = np.array([actual[mask][selected_dates == day].sum() for day in unique_dates], dtype=float)
                    daily_count = np.array([(selected_dates == day).sum() for day in unique_dates], dtype=float)
                    draws = rng.integers(0, len(unique_dates), size=(300, len(unique_dates)))
                    rates = daily_success[draws].sum(axis=1) / np.maximum(daily_count[draws].sum(axis=1), 1.0)
                    row["date_block_ci_low"] = float(np.quantile(rates, 0.025))
                    row["date_block_ci_high"] = float(np.quantile(rates, 0.975))
                    row["unique_dates"] = int(len(unique_dates))
            bins.append(row)
            weighted_gap += count / len(actual) * abs(predicted_mean - observed_rate)
        curves[str(class_name)] = bins
        class_eces.append(weighted_gap)
    return {"calibration_curve": curves, "calibration_ece": float(np.mean(class_eces)) if class_eces else None,
        "calibration_bin_policy": "fixed 0.1 bins; classwise ECE averaged across classes",
        "calibration_ci_policy": "whole-date block bootstrap, 300 draws" if block_ci else "not included in per-model summary"}


def train_bundle(samples: pd.DataFrame, features: list[str], horizon: int, strict_time: bool = False,
                 target_col: str = "label") -> ModelBundle | None:
    if target_col not in samples:
        return None
    valid = samples.dropna(subset=[target_col]).copy()
    valid = valid[valid[features].notna().sum(axis=1) >= max(5, int(len(features) * 0.65))]
    if valid.empty or valid[target_col].nunique() < (2 if strict_time else 3):
        return None
    dates = pd.to_datetime(valid["date"])
    max_date = dates.max()
    test_start = max_date - pd.DateOffset(months=6)
    cal_start = max_date - pd.DateOffset(months=12)
    train = valid[dates < cal_start]
    cal = valid[(dates >= cal_start) & (dates < test_start)]
    test = valid[dates >= test_start]
    if strict_time:
        if "label_end" not in valid:
            raise ValueError("Strict time calibration requires actual label maturity dates")
        train = train[pd.to_datetime(train["label_end"]) < cal_start]
        cal = cal[pd.to_datetime(cal["label_end"]) < test_start]
        if len(train) < 300 or len(cal) < 60 or len(test) < 60 or train[target_col].nunique() < 2:
            return None
        if not set(valid[target_col]).issubset(set(train[target_col])):
            return None
    if not strict_time and (len(train) < 300 or len(cal) < 100 or len(test) < 100):
        # Keep the time order but mark the eventual output low confidence.
        cut = int(len(valid) * 0.7); cal_cut = int(len(valid) * 0.85)
        train, cal, test = valid.iloc[:cut], valid.iloc[cut:cal_cut], valid.iloc[cal_cut:]
    if train[target_col].nunique() < (2 if strict_time else 3) or len(cal) < 20:
        return None
    medians = train[features].median(numeric_only=True).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    scaler = StandardScaler().fit(train[features].fillna(medians))
    # Current scikit-learn defaults to multinomial behaviour for lbfgs on the
    # multiclass case; avoid the removed multi_class keyword for newer builds.
    model = LogisticRegression(max_iter=700, solver="lbfgs", class_weight="balanced", random_state=7)
    model.fit(scaler.transform(train[features].fillna(medians)), train[target_col])
    cal_raw = model.predict_proba(scaler.transform(cal[features].fillna(medians)))
    calibrators = {}
    for i, cls in enumerate(model.classes_):
        ir = IsotonicRegression(out_of_bounds="clip")
        ir.fit(cal_raw[:, i], (cal[target_col].to_numpy() == cls).astype(float))
        calibrators[str(cls)] = ir
    test_metrics: dict[str, Any] = {"horizon": horizon, "target": target_col, "classes": [str(c) for c in model.classes_],
        "train_n": len(train), "calibration_n": len(cal), "test_n": len(test), "ambiguous_excluded": int(samples["ambiguous"].sum()),
        "train_class_rates": {str(cls): float((train[target_col].astype(str) == str(cls)).mean()) for cls in model.classes_}}
    if strict_time:
        test_metrics.update({"purged": True, "train_label_end": str(pd.to_datetime(train['label_end']).max().date()),
            "calibration_start": str(pd.to_datetime(cal['date']).min().date()),
            "calibration_label_end": str(pd.to_datetime(cal['label_end']).max().date()),
            "test_start": str(pd.to_datetime(test['date']).min().date()), "classes": list(model.classes_)})
    if len(test):
        test_raw = model.predict_proba(scaler.transform(test[features].fillna(medians)))
        test_cal = np.column_stack([calibrators[str(cls)].predict(test_raw[:, i]) for i, cls in enumerate(model.classes_)])
        if strict_time:
            empty_mass = test_cal.sum(axis=1) <= 0
            test_cal[empty_mass] = test_raw[empty_mass]
        test_cal = test_cal / np.maximum(test_cal.sum(axis=1, keepdims=True), 1e-9)
        y = pd.Categorical(test[target_col], categories=model.classes_).codes
        one_hot = np.eye(len(model.classes_))[y]
        predicted = model.classes_[np.argmax(test_cal, axis=1)]
        baseline_class = str(train[target_col].value_counts().idxmax())
        baseline_accuracy = float(np.mean(test[target_col].astype(str).to_numpy() == baseline_class))
        diagnostics = calibration_diagnostics(test_cal, test[target_col], [str(value) for value in model.classes_],
            dates=test.get("date"), block_ci=False)
        test_metrics.update({
            "brier_multiclass": float(np.mean(np.sum((test_cal - one_hot) ** 2, axis=1))),
            "log_loss": float(log_loss(test[target_col], test_cal, labels=list(model.classes_))),
            "classification_accuracy": float(np.mean(predicted == test[target_col].to_numpy())),
            "majority_baseline_class": baseline_class,
            "majority_baseline_accuracy": baseline_accuracy,
            "accuracy_lift_vs_majority_baseline": float(np.mean(predicted == test[target_col].to_numpy())) - baseline_accuracy,
            "unique_test_dates": int(pd.to_datetime(test["date"]).nunique()) if "date" in test else None,
            "probability_margin_threshold": None,
            "test_start": str(pd.to_datetime(test["date"]).min().date()),
            "test_end": str(pd.to_datetime(test["date"]).max().date()),
            **diagnostics,
        })
        margins = np.sort(test_cal, axis=1)[:, -1] - np.sort(test_cal, axis=1)[:, -2]
        correct_margins = margins[predicted == test[target_col].to_numpy()]
        test_metrics["probability_margin_threshold"] = float(np.quantile(correct_margins, 0.25)) if len(correct_margins) else float(np.quantile(margins, 0.75))
        if strict_time and set(model.classes_).issubset({'00','01','10','11'}):
            for side, index in [('bottom', 0), ('top', 1)]:
                probability = test_cal[:, [str(c)[index] == '1' for c in model.classes_]].sum(axis=1)
                actual = np.array([str(c)[index] == '1' for c in test[target_col]], dtype=float)
                test_metrics[f'{side}_brier'] = float(np.mean((probability-actual)**2))
                test_metrics[f'{side}_observed_rate'] = float(actual.mean())
                test_metrics[f'{side}_predicted_mean'] = float(probability.mean())
    return ModelBundle(scaler, model, calibrators, features, medians, test_metrics)


def _plain_quantile(values: pd.Series, q: float) -> float | None:
    x = pd.to_numeric(values, errors="coerce").dropna()
    return float(x.quantile(q)) if len(x) else None


def _class_path_bands(history: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Build class-conditional relative extreme bands from matured history only."""
    result: dict[str, dict[str, Any]] = {}
    valid = history.dropna(subset=["adj_close", "min_price", "max_price", "label"]).copy()
    valid = valid[(valid["adj_close"] > 0) & valid["ambiguous"].eq(False)].copy()
    if valid.empty:
        return result
    valid["bottom_ratio"] = valid["min_price"] / valid["adj_close"]
    valid["top_ratio"] = valid["max_price"] / valid["adj_close"]
    for label in ("upfirst", "downfirst", "unhit"):
        subset = valid[valid["label"] == label]
        if len(subset) < 20:
            subset = valid
        result[label] = {
            "bottom_ratio": [_plain_quantile(subset["bottom_ratio"], 0.25), _plain_quantile(subset["bottom_ratio"], 0.75)],
            "top_ratio": [_plain_quantile(subset["top_ratio"], 0.25), _plain_quantile(subset["top_ratio"], 0.75)],
            "history_n": int(len(subset)),
        }
    return result


def _distance_to_band(value: float | None, band: list[float] | None) -> float | None:
    if value is None or not isinstance(band, list) or len(band) != 2 or not all(clean_num(v) is not None for v in band):
        return None
    lo, hi = float(band[0]), float(band[1])
    if lo <= value <= hi:
        return 0.0
    return float(min(abs(value - lo), abs(value - hi)) / max(abs(value), 1e-9))


def one_year_walk_forward_backtest(samples: dict[int, pd.DataFrame]) -> dict[str, Any]:
    """Evaluate the free-data B3 path model on the most recent year.

    Each quarterly test fold is trained only on observations before the fold
    after a horizon-sized purge. Price-band accuracy is a diagnostic of
    class-conditional path bands, not a promise that a turning point will
    reverse there. Checkpoints are sampled every fifth session per symbol to
    avoid pretending highly correlated daily observations are independent.
    """
    output: dict[str, Any] = {
        "status": "observed",
        "data_source": "Yahoo Finance chart OHLCV via yfinance; daily adjusted OHLC path",
        "evaluation_window": "most recent 12 months of matured labels",
        "fold_policy": "four chronological quarterly folds; train/calibration before fold with horizon-session purge",
        "checkpoint_policy": "all matured rows for class metrics; every fifth session per symbol for price-band distance",
        "targets": {"classification_accuracy": 0.70, "bottom_distance_within_3pct": 0.70, "top_distance_within_3pct": 0.70},
        "horizons": {},
    }
    for horizon in HORIZONS:
        source = samples.get(horizon, pd.DataFrame()).copy()
        if source.empty:
            output["horizons"][str(horizon)] = {"status": "insufficient_training_data"}
            continue
        valid = source.dropna(subset=["date", "label", "adj_close", "min_price", "max_price"]).copy()
        valid = valid[(valid["ambiguous"] == False) & (valid[BASE_FEATURES].notna().sum(axis=1) >= max(5, int(len(BASE_FEATURES) * 0.65)))]
        if valid.empty:
            output["horizons"][str(horizon)] = {"status": "insufficient_training_data"}
            continue
        valid["date"] = pd.to_datetime(valid["date"])
        end_date = valid["date"].max()
        start_date = end_date - pd.DateOffset(months=12)
        edges = [start_date + pd.DateOffset(months=3 * i) for i in range(5)]
        edges[-1] = end_date + pd.Timedelta(days=1)
        all_true, all_prob, all_pred, all_dates = [], [], [], []
        band_rows: list[dict[str, Any]] = []
        fold_reports: list[dict[str, Any]] = []
        for fold in range(4):
            fold_start, fold_end = edges[fold], edges[fold + 1]
            purge_cutoff = fold_start - pd.tseries.offsets.BDay(horizon)
            train = valid[valid["date"] < purge_cutoff].copy()
            test = valid[(valid["date"] >= fold_start) & (valid["date"] < fold_end)].copy()
            bundle = train_bundle(train, BASE_FEATURES, horizon) if len(train) else None
            if bundle is None or test.empty:
                fold_reports.append({"fold": fold + 1, "status": "insufficient_training_data", "train_n": int(len(train)), "test_n": int(len(test))})
                continue
            history = valid[valid["date"] < purge_cutoff].copy()
            class_bands = _class_path_bands(history)
            probs, preds, truths = [], [], []
            for _, row in test.iterrows():
                probability = bundle.predict(row)
                prob_map = {str(cls): float(probability[i]) for i, cls in enumerate(bundle.model.classes_)}
                prediction = max(prob_map, key=prob_map.get)
                probs.append([prob_map.get(cls, 0.0) for cls in ("downfirst", "unhit", "upfirst")])
                preds.append(prediction)
                truths.append(str(row["label"]))
                all_dates.append(str(row["date"].date()))
            all_prob.extend(probs)
            all_pred.extend(preds)
            all_true.extend(truths)
            checkpoint = test.sort_values(["symbol", "date"]).copy()
            checkpoint["session_rank"] = checkpoint.groupby("symbol").cumcount()
            checkpoint = checkpoint[(checkpoint["session_rank"] % 5 == 0) | (checkpoint["session_rank"] == checkpoint.groupby("symbol")["session_rank"].transform("max"))]
            for _, row in checkpoint.iterrows():
                probability = bundle.predict(row)
                classes = list(bundle.model.classes_)
                predicted_label = str(classes[int(np.argmax(probability))])
                bands = class_bands.get(predicted_label) or class_bands.get("unhit") or {}
                ref = clean_num(row.get("adj_close"))
                if not ref:
                    continue
                bottom_ratio, top_ratio = bands.get("bottom_ratio"), bands.get("top_ratio")
                bottom_band = [ref * float(bottom_ratio[0]), ref * float(bottom_ratio[1])] if isinstance(bottom_ratio, list) and all(clean_num(v) is not None for v in bottom_ratio) else None
                top_band = [ref * float(top_ratio[0]), ref * float(top_ratio[1])] if isinstance(top_ratio, list) and all(clean_num(v) is not None for v in top_ratio) else None
                actual_bottom = clean_num(row.get("min_price"))
                actual_top = clean_num(row.get("max_price"))
                bottom_distance = _distance_to_band(actual_bottom, bottom_band)
                top_distance = _distance_to_band(actual_top, top_band)
                band_rows.append({"date": str(row["date"].date()), "symbol": row["symbol"], "bottom_distance": bottom_distance, "top_distance": top_distance, "bottom_within_3pct": bottom_distance is not None and bottom_distance <= 0.03, "top_within_3pct": top_distance is not None and top_distance <= 0.03})
            fold_reports.append({"fold": fold + 1, "status": "observed", "train_n": int(len(train)), "test_n": int(len(test)), "test_start": str(test["date"].min().date()), "test_end": str(test["date"].max().date()), "band_checkpoint_n": int(len(checkpoint))})
        if not all_true:
            output["horizons"][str(horizon)] = {"status": "insufficient_training_data", "folds": fold_reports}
            continue
        classes = ["downfirst", "unhit", "upfirst"]
        y_one = np.eye(3)[[classes.index(x) for x in all_true]]
        prob_array = np.asarray(all_prob, dtype=float)
        prob_array = prob_array / np.maximum(prob_array.sum(axis=1, keepdims=True), 1e-9)
        band_df = pd.DataFrame(band_rows)
        bottom_dist = pd.to_numeric(band_df.get("bottom_distance", pd.Series(dtype=float)), errors="coerce").dropna()
        top_dist = pd.to_numeric(band_df.get("top_distance", pd.Series(dtype=float)), errors="coerce").dropna()
        output["horizons"][str(horizon)] = {
            "status": "observed",
            "evaluation_start": str(start_date.date()),
            "evaluation_end": str(end_date.date()),
            "matured_classification_n": len(all_true),
            "unique_evaluation_dates": len(set(all_dates)),
            "band_checkpoint_n": int(len(band_df)),
            "classification_accuracy": float(np.mean(np.asarray(all_pred) == np.asarray(all_true))),
            "brier_multiclass": float(np.mean(np.sum((prob_array - y_one) ** 2, axis=1))),
            "log_loss": float(log_loss(all_true, prob_array, labels=classes)),
            "bottom_distance_within_3pct": float(np.mean(band_df["bottom_within_3pct"])) if len(band_df) else None,
            "top_distance_within_3pct": float(np.mean(band_df["top_within_3pct"])) if len(band_df) else None,
            "bottom_distance_median": float(bottom_dist.median()) if len(bottom_dist) else None,
            "top_distance_median": float(top_dist.median()) if len(top_dist) else None,
            "folds": fold_reports,
            "note": "价格带诊断按预测类别的历史路径相对极值分位构建；到达/极值距离不是反转确认，也不是交易成功率。分类日样本存在相关性，band checkpoint按每只股票每5个交易日取样。",
        }
    return output


def one_year_directional_turn_backtest(samples: dict[int, pd.DataFrame]) -> dict[str, Any]:
    """Walk-forward validation for the exclusive directional-turn challenger."""
    classes = ["bottom_rebound_first", "no_directional_turn", "top_reversal_first"]
    output: dict[str, Any] = {
        "status": "observed",
        "model_version": DIRECTIONAL_MODEL_VERSION,
        "target": "first 1-ATR boundary plus same-window reversal confirmation",
        "classes": classes,
        "sum_rule": "mutually exclusive probabilities sum to 1",
        "evaluation_window": "most recent 12 months of matured labels",
        "fold_policy": "four chronological quarterly folds; horizon-session purge before every test fold",
        "horizons": {},
    }
    for horizon in HORIZONS:
        source = samples.get(horizon, pd.DataFrame()).copy()
        if source.empty or "turn_label" not in source:
            output["horizons"][str(horizon)] = {"status": "insufficient_training_data"}
            continue
        valid = source.dropna(subset=["date", "turn_label"]).copy()
        valid = valid[(valid["ambiguous"] == False) & (valid[BASE_FEATURES].notna().sum(axis=1) >= max(5, int(len(BASE_FEATURES) * 0.65)))]
        valid["date"] = pd.to_datetime(valid["date"])
        if valid.empty:
            output["horizons"][str(horizon)] = {"status": "insufficient_training_data"}
            continue
        end_date = valid["date"].max()
        start_date = end_date - pd.DateOffset(months=12)
        edges = [start_date + pd.DateOffset(months=3 * i) for i in range(5)]
        edges[-1] = end_date + pd.Timedelta(days=1)
        truths: list[str] = []
        probabilities: list[list[float]] = []
        baseline_predictions: list[str] = []
        evaluation_dates: list[str] = []
        folds: list[dict[str, Any]] = []
        for fold in range(4):
            fold_start, fold_end = edges[fold], edges[fold + 1]
            purge_cutoff = fold_start - pd.tseries.offsets.BDay(horizon)
            train = valid[valid["date"] < purge_cutoff].copy()
            test = valid[(valid["date"] >= fold_start) & (valid["date"] < fold_end)].copy()
            bundle = train_bundle(train, BASE_FEATURES, horizon, target_col="turn_label") if len(train) else None
            if bundle is None or test.empty:
                folds.append({"fold": fold + 1, "status": "insufficient_training_data", "train_n": int(len(train)), "test_n": int(len(test))})
                continue
            baseline_class = str(train["turn_label"].value_counts().idxmax())
            for _, row in test.iterrows():
                predicted = bundle.predict(row)
                mapped = dict(zip((str(c) for c in bundle.model.classes_), predicted))
                probabilities.append([float(mapped.get(cls, 0.0)) for cls in classes])
                truths.append(str(row["turn_label"]))
                baseline_predictions.append(baseline_class)
                evaluation_dates.append(str(row["date"].date()))
            folds.append({"fold": fold + 1, "status": "observed", "train_n": int(len(train)), "test_n": int(len(test)),
                "test_start": str(test["date"].min().date()), "test_end": str(test["date"].max().date())})
        if not truths:
            output["horizons"][str(horizon)] = {"status": "insufficient_training_data", "folds": folds}
            continue
        probability_array = np.asarray(probabilities, dtype=float)
        probability_array /= np.maximum(probability_array.sum(axis=1, keepdims=True), 1e-9)
        actual = np.eye(len(classes))[[classes.index(value) for value in truths]]
        predicted = np.asarray(classes)[np.argmax(probability_array, axis=1)]
        accuracy = float(np.mean(predicted == np.asarray(truths)))
        baseline_accuracy = float(np.mean(np.asarray(baseline_predictions) == np.asarray(truths)))
        diagnostics = calibration_diagnostics(probability_array, truths, classes, evaluation_dates, block_ci=True)
        margins = np.sort(probability_array, axis=1)[:, -1] - np.sort(probability_array, axis=1)[:, -2]
        correct_margins = margins[predicted == np.asarray(truths)]
        decision_margin_threshold = float(np.quantile(correct_margins, 0.25)) if len(correct_margins) else float(np.quantile(margins, 0.75))
        output["horizons"][str(horizon)] = {
            "status": "observed",
            "matured_classification_n": len(truths),
            "classification_accuracy": accuracy,
            "majority_baseline_accuracy": baseline_accuracy,
            "accuracy_lift_vs_majority_baseline": accuracy - baseline_accuracy,
            "unique_evaluation_dates": int(len(set(evaluation_dates))),
            "brier_multiclass": float(np.mean(np.sum((probability_array - actual) ** 2, axis=1))),
            "log_loss": float(log_loss(truths, probability_array, labels=classes)),
            "decision_margin_threshold": decision_margin_threshold,
            "decision_margin_threshold_policy": "25th percentile of correct out-of-sample top1-top2 margins; fallback 75th percentile of all margins",
            "observed_class_rates": {cls: float(np.mean(np.asarray(truths) == cls)) for cls in classes},
            "folds": folds,
            **diagnostics,
            "limitations": "overlapping daily outcomes are correlated; no 70% claim; direction classes do not replace B3 opportunity/risk",
        }
    return output


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
            labeled = add_directional_turn_label(labeled)
            labeled["symbol"], labeled["date"] = symbol, labeled.index
            sample_columns = list(dict.fromkeys(BASE_FEATURES + ["adj_close", "atr", "label", "turn_label", "ambiguous", "symbol", "date", "terminal_return", "min_drawdown", "min_price", "max_price", "bottom_event", "top_event"]))
            all_samples[h].append(labeled[sample_columns].copy())
        feature_frames[symbol] = f
    return {h: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for h, v in all_samples.items()}, feature_frames


def confidence_from_path_validation(distances: np.ndarray, validation: dict[str, Any] | None) -> dict[str, Any]:
    """Grade evidence quality without converting it into a turning probability.

    High confidence is deliberately gated on positive recent validation lift
    over the training-period majority-class baseline. Medium confidence means
    the path support and calibration are usable but predictive lift is not yet
    strong enough for the high tier. This score never changes the probability,
    opportunity, risk, or ranking values.
    """
    finite_distances = np.sort(np.asarray(distances, dtype=float)[np.isfinite(distances)])
    nearest = finite_distances[: min(40, len(finite_distances))]
    path_score = float(100.0 * np.mean(np.exp(-np.clip(nearest, 0.0, 8.0)))) if len(nearest) else 0.0
    nearest_distance = float(nearest[0]) if len(nearest) else None
    validation = validation or {}
    ece = clean_num(validation.get("calibration_ece"))
    accuracy = clean_num(validation.get("classification_accuracy"))
    baseline = clean_num(validation.get("majority_baseline_accuracy"))
    lift = clean_num(validation.get("accuracy_lift_vs_majority_baseline"))
    if lift is None and accuracy is not None and baseline is not None:
        lift = accuracy - baseline
    test_n = int(validation.get("test_n") or 0)
    unique_dates = int(validation.get("unique_test_dates") or 0)
    calibration_score = max(0.0, 100.0 * (1.0 - min(1.0, (ece if ece is not None else 0.30) / 0.25)))
    skill_score = max(0.0, min(100.0, 50.0 + 500.0 * (lift if lift is not None else -0.10)))
    support_score = min(100.0, 100.0 * min(test_n / 120.0, unique_dates / 60.0 if unique_dates else 0.0))
    regime_shift = bool(path_score < 25.0 or (nearest_distance is not None and nearest_distance > 2.5))
    score = float(np.clip(0.40 * path_score + 0.35 * calibration_score + 0.15 * skill_score + 0.10 * support_score, 0.0, 100.0))
    enough_support = test_n >= 60 and unique_dates >= 40
    if enough_support and not regime_shift and score >= 75 and path_score >= 55 and calibration_score >= 70 and lift is not None and lift >= 0.03:
        level, label = "high", "高确信"
        reason = "历史路径重合较高，近期独立测试校准较好，且准确率明确高于训练期多数类基线。"
    elif enough_support and not regime_shift and score >= 50 and path_score >= 35 and calibration_score >= 50:
        level, label = "medium", "中确信"
        reason = ("历史路径重合与近期校准可用，但相对多数类基线的增益尚不足以进入高确信。" if lift is None or lift < 0.03
                  else "历史路径与近期校准支持中等，仍未满足高确信的全部门槛。")
    else:
        level, label = "low", "低可信/无明显偏向"
        reason = ("当前状态偏离可用历史近邻，可能存在体制切换。" if regime_shift else
                  "近期校准、基线增益或真实测试日期支持不足。")
    return {"confidence_method_version": CONFIDENCE_METHOD_VERSION, "confidence_level": level,
        "confidence_label": label, "confidence_score": score, "path_similarity_score": path_score,
        "nearest_path_distance": nearest_distance, "validation_calibration_score": calibration_score,
        "validation_ece": ece, "validation_accuracy": accuracy, "validation_majority_baseline_accuracy": baseline,
        "validation_accuracy_lift": lift, "validation_test_n": test_n, "validation_unique_dates": unique_dates,
        "regime_shift_flag": regime_shift, "confidence_reason": reason,
        "confidence_note": "信度只评价证据质量，不改变方向概率，也不是预期收益或胜率。"}


def _valid_band(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    nums = [clean_num(v) for v in value]
    if any(v is None for v in nums) or nums[0] > nums[1] or nums[0] <= 0:
        return None
    return [float(nums[0]), float(nums[1])]


def _weighted_quantile_array(values: np.ndarray, weights: np.ndarray, q: float) -> float | None:
    if len(values) == 0:
        return None
    try:
        return clean_num(weighted_quantile(values, weights, q))
    except (ValueError, FloatingPointError):
        return None


def _risk_and_return_audit(terminal: np.ndarray, mins: np.ndarray, ref: float,
                           weights: np.ndarray, net: np.ndarray) -> dict[str, Any]:
    """Return raw tail metrics plus explicit unit validation.

    Raw values remain available when the ordinary-long interpretation is
    invalid. Clipping would hide a path-generation or unit problem.
    """
    tail_n = max(1, int(math.ceil(len(net) * 0.05)))
    order = np.argsort(net)
    loss_tail = order[:tail_n]
    upside_order = order[-tail_n:]
    dd = np.maximum(0.0, 1.0 - mins / max(ref, 1e-9))
    dd_tail = np.sort(dd)[-tail_n:]
    raw_risk = float(np.mean(dd_tail)) if len(dd_tail) else None
    weighted_loss = np.maximum(0.0, -net)
    positive = np.maximum(0.0, net)
    expected = float(np.sum(weights * net))
    tail_contribution = float(np.sum(weights[upside_order] * positive[upside_order])) if len(upside_order) else 0.0
    mean_tail_share = tail_contribution / expected if expected > 0 else None
    invalid_reasons: list[str] = []
    if not math.isfinite(ref) or ref <= 0:
        invalid_reasons.append("reference_price_nonpositive")
    if not np.isfinite(mins).all() or not np.isfinite(terminal).all():
        invalid_reasons.append("nonfinite_path_price")
    if np.nanmin(mins) < 0:
        invalid_reasons.append("negative_modeled_price")
    if np.nanmax(terminal) <= 0:
        invalid_reasons.append("nonpositive_terminal_price")
    risk_valid = not invalid_reasons
    return {
        "risk_value_raw": raw_risk,
        "es95_raw": raw_risk,
        "risk_metric_definition": "ordinary_unlevered_long_simple_price_drawdown_mean_of_worst_5pct_paths",
        "risk_metric_unit": "fraction_of_reference_price",
        "risk_metric_tail_probability": 0.05,
        "risk_metric_valid": risk_valid,
        "risk_metric_validation_reason": "valid" if risk_valid else ";".join(invalid_reasons),
        "expected_return_weighted_mean": expected,
        "expected_return_median": _weighted_quantile_array(net, weights, 0.50),
        "expected_return_p25": _weighted_quantile_array(net, weights, 0.25),
        "expected_return_p75": _weighted_quantile_array(net, weights, 0.75),
        "expected_return_p05": _weighted_quantile_array(net, weights, 0.05),
        "expected_return_p95": _weighted_quantile_array(net, weights, 0.95),
        "probability_of_loss": float(np.sum(weights[net < 0])) if len(net) else None,
        "expected_shortfall": float(np.sum(weights[loss_tail] * weighted_loss[loss_tail]) / max(np.sum(weights[loss_tail]), 1e-9)) if len(loss_tail) else None,
        "upside_tail_contribution": tail_contribution,
        "top_5pct_paths_contribution_to_mean": mean_tail_share,
        "mean_tail_driven": bool(mean_tail_share is not None and mean_tail_share > 0.50),
    }


def _sample_audit(candidate: pd.DataFrame, records: list[dict[str, Any]], weights: np.ndarray,
                  history_source: str, own_history_count: int | None = None,
                  peer_transfer_count: int | None = None) -> dict[str, Any]:
    dates = pd.to_datetime(candidate.get("date"), errors="coerce").dropna() if "date" in candidate else pd.Series(dtype="datetime64[ns]")
    selected_dates = dates.iloc[:len(records)] if len(dates) >= len(records) else dates
    labels = candidate.get("label", pd.Series(dtype=object)).dropna()
    max_weight = float(np.max(weights)) if len(weights) else None
    top_n = max(1, int(math.ceil(len(weights) * 0.05))) if len(weights) else 0
    top_share = float(np.sort(weights)[-top_n:].sum()) if top_n else None
    return {
        "history_source": history_source,
        "own_history_sample_count": int(own_history_count if own_history_count is not None else len(candidate)),
        "peer_transfer_sample_count": int(peer_transfer_count or 0),
        "nominal_path_count": int(len(records)),
        "effective_weighted_sample_size": float(1.0 / np.sum(weights ** 2)) if len(weights) else None,
        "effective_scenario_n": float(1.0 / np.sum(weights ** 2)) if len(weights) else None,
        "unique_trade_dates": int(selected_dates.nunique()),
        "unique_regimes": int(labels.nunique()),
        "max_single_path_weight": max_weight,
        "top_5_weights_share": top_share,
        "sample_audit_note": "ESS只影响决策资格，不重写原始概率；unique_regimes为可审计标签状态数，不宣称独立体制样本数。",
    }


def _decision_fields(metric: dict[str, Any], current: pd.Series, margin_threshold: float | None = None) -> dict[str, Any]:
    """Derive a decision layer without using opportunity minus risk."""
    bottom = clean_num(metric.get("p_bottom_rebound_first")); top = clean_num(metric.get("p_top_reversal_first")); no_turn = clean_num(metric.get("p_no_directional_turn"))
    probs = {"bottom_reversal_candidate": bottom, "top_reversal_warning": top, "direction_unclear": no_turn}
    ordered = sorted(((k, v) for k, v in probs.items() if v is not None), key=lambda kv: (-kv[1], kv[0]))
    top1 = ordered[0][1] if ordered else None; top2 = ordered[1][1] if len(ordered) > 1 else None
    margin = top1 - top2 if top1 is not None and top2 is not None else None
    baseline = metric.get("directional_baseline_rates") or {}
    edge = {
        "bottom_reversal_candidate": bottom - (clean_num(baseline.get("bottom_rebound_first", 0.0)) or 0.0) if bottom is not None else None,
        "top_reversal_warning": top - (clean_num(baseline.get("top_reversal_first", 0.0)) or 0.0) if top is not None else None,
        "direction_unclear": no_turn - (clean_num(baseline.get("no_directional_turn", 0.0)) or 0.0) if no_turn is not None else None,
    }
    reasons: list[str] = []
    if metric.get("risk_metric_valid") is False:
        reasons.extend(["data_quality_failure", "metric_unit_anomaly"])
    if clean_num(metric.get("effective_weighted_sample_size")) is None or float(metric.get("effective_weighted_sample_size", 0)) < 40:
        reasons.append("insufficient_effective_samples")
    if metric.get("history_source") != "own_history":
        reasons.append("symbol_history_issue")
    ece = clean_num(metric.get("validation_ece"))
    if ece is None or ece > 0.20:
        reasons.append("poor_calibration")
    if metric.get("regime_shift_flag") is True:
        reasons.append("regime_drift")
    if margin_threshold is not None and margin is not None and margin < margin_threshold:
        reasons.append("low_directional_separation")
    if margin_threshold is None:
        reasons.append("horizon_mismatch")
    bottom_zone = _valid_band(metric.get("bottom_zone")); top_zone = _valid_band(metric.get("top_zone"))
    eligible = not reasons and bool(ordered) and bottom_zone is not None and top_zone is not None
    if bottom_zone is None or top_zone is None:
        reasons.append("data_quality_failure")
    if eligible and clean_num(metric.get("p_two_way_wash")) is not None and float(metric["p_two_way_wash"]) >= 0.40 and metric.get("two_way_wash_status") == "scenario_diagnostic_not_direction_probability":
        primary = "two_way_high_volatility_wash"
    elif not eligible:
        primary = "data_model_pending_review"
    elif bottom is not None and top is not None and bottom > top and (margin_threshold is None or bottom - top >= margin_threshold):
        primary = "bottom_reversal_candidate"
    elif top is not None and bottom is not None and top > bottom and (margin_threshold is None or top - bottom >= margin_threshold):
        primary = "top_reversal_warning"
    else:
        trend = clean_num(current.get("dist_ma20")); ret20 = clean_num(current.get("ret_20"))
        if no_turn is not None and no_turn >= max(bottom or 0.0, top or 0.0) and trend is not None and ret20 is not None and trend > 0 and ret20 > 0:
            primary = "trend_continuation_up"
        elif no_turn is not None and no_turn >= max(bottom or 0.0, top or 0.0) and trend is not None and ret20 is not None and trend < 0 and ret20 < 0:
            primary = "trend_continuation_down"
        else:
            primary = "direction_unclear"
    return {
        "stage_primary_judgment": primary,
        "stage_primary_judgment_label": {"bottom_reversal_candidate":"底部反转候选","top_reversal_warning":"顶部反转警告","two_way_high_volatility_wash":"双向高波动/洗盘","trend_continuation_up":"上行趋势延续","trend_continuation_down":"下行趋势延续","direction_unclear":"方向不明确","data_model_pending_review":"数据/模型待复核"}.get(primary, "数据/模型待复核"),
        "decision_eligible": bool(eligible),
        "decision_block_reason": sorted(set(reasons)),
        "directional_edge_vs_baseline": edge,
        "probability_margin_top1_top2": margin,
        "probability_margin_threshold_backtest": margin_threshold,
        "decision_rule_note": "阶段主判断只由方向模型、候选区、证据质量和数据质量生成；不使用机会分减风险分。",
    }


def build_scenario_metric(symbol: str, current: pd.Series, historical: pd.DataFrame, bundle: ModelBundle | None, horizon: int,
                          current_frame: pd.DataFrame, event_bundle: ModelBundle | None = None, relative_atr: bool = False,
                          directional_bundle: ModelBundle | None = None, history_source: str = "own_history",
                          peer_transfer_count: int = 0, margin_threshold: float | None = None) -> dict[str, Any]:
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
    own_history_count = int(len(candidate)) if history_source == "own_history" else 0
    cur_vec = current.reindex(features).astype(float)
    med = candidate[features].median(numeric_only=True).fillna(0.0)
    mat = candidate[features].fillna(med).to_numpy(dtype=float)
    vec = cur_vec.fillna(med).to_numpy(dtype=float)
    scale = np.nanstd(mat, axis=0); scale[~np.isfinite(scale) | (scale == 0)] = 1.0
    dist = np.sqrt(np.nanmean(((mat - vec) / scale) ** 2, axis=1))
    candidate = candidate.assign(_distance=dist).sort_values(["_distance", "date"], kind="mergesort").head(160)
    confidence = confidence_from_path_validation(candidate["_distance"].to_numpy(dtype=float),
        directional_bundle.test_metrics if directional_bundle is not None else None)
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
        scale = min(2.0, max(0.5, scale_ratio))
        origin = max(float(row["adj_close"]), 1e-9)
        # Positive-price assets need multiplicative path scaling. The former
        # linear distance extrapolation could turn a valid historical low into
        # a negative modeled price when current ATR was much larger.
        terminal = ref * max(float(row["terminal"]) / origin, 1e-9) ** scale if "terminal" in row else ref * max(1.0 + float(row["terminal_return"]), 1e-9) ** scale
        min_price = ref * max(float(row["min_price"]) / origin, 1e-9) ** scale
        max_price = ref * max(float(row["max_price"]) / origin, 1e-9) ** scale
        if relative_atr:
            terminal = ref * max(1.0 + float(row['terminal_return']), 1e-9) ** scale
            min_price = ref * max(float(row['min_price']) / origin, 1e-9) ** scale
            max_price = ref * max(float(row['max_price']) / origin, 1e-9) ** scale
            if min_price <= 0 or not min_price <= terminal <= max_price:
                continue
        dd = max(0.0, 1 - min_price / ref)
        records.append({"label": cls, "weight": weight, "terminal": terminal, "min_price": min_price, "max_price": max_price, "drawdown": dd, "bottom": bool(row["bottom_event"]), "top": bool(row["top_event"])})
    if not records:
        return {"status": "structural_only"}
    weights = np.array([r["weight"] for r in records], dtype=float); weights /= weights.sum()
    if event_bundle is not None:
        # A calibrated four-state joint event distribution preserves independent
        # bottom/top marginals while keeping all public outputs on one path set.
        states = np.array([f"{int(r['bottom'])}{int(r['top'])}" for r in records])
        target = dict(zip(event_bundle.model.classes_, event_bundle.predict(current)))
        for state, probability in target.items():
            if probability > 1e-8 and not np.any(states == state):
                return {"status":"uncalibrated", "reason":"calibrated joint event has no supporting historical paths"}
        for state in np.unique(states):
            mask = states == state
            weights[mask] *= float(target.get(state, 0.0)) / weights[mask].sum()
        if weights.sum() <= 0:
            return {"status":"uncalibrated", "reason":"no calibrated path mass"}
        weights /= weights.sum()
        if 1 / np.sum(weights ** 2) < 20:
            return {"status":"uncalibrated", "reason":"effective scenario support below 20"}
    terminal = np.array([r["terminal"] for r in records]); mins = np.array([r["min_price"] for r in records]); maxs = np.array([r["max_price"] for r in records]); dds = np.array([r["drawdown"] for r in records])
    net = terminal / ref - 1 - 0.0015
    expected = float(np.sum(weights * net)); sd = float(np.sqrt(np.sum(weights * (net - expected) ** 2)))
    eff_n = float(1 / np.sum(weights ** 2)); mu_lcb = expected - 1.645 * sd / math.sqrt(max(eff_n, 1))
    risk = float(np.mean(np.sort(dds)[-max(1, int(math.ceil(len(dds) * 0.05))):]))
    opportunity = mu_lcb / max(risk, 0.02)
    risk_audit = _risk_and_return_audit(terminal, mins, ref, weights, net)
    sample_audit = _sample_audit(candidate, records, weights, history_source, own_history_count, peer_transfer_count)
    bottom_mask = np.array([r["bottom"] for r in records], dtype=bool); top_mask = np.array([r["top"] for r in records], dtype=bool)
    def event_prob(mask: np.ndarray) -> float: return float(np.sum(weights[mask])) if mask.any() else 0.0
    directional = {"p_bottom_rebound_first": None, "p_top_reversal_first": None, "p_no_directional_turn": None,
        "directional_turn_status": "uncalibrated"}
    if directional_bundle is not None:
        predicted = directional_bundle.predict(current)
        mapped = {str(cls): float(predicted[i]) for i, cls in enumerate(directional_bundle.model.classes_)}
        triple = np.array([mapped.get("bottom_rebound_first", 0.0), mapped.get("top_reversal_first", 0.0), mapped.get("no_directional_turn", 0.0)], dtype=float)
        if np.isfinite(triple).all() and triple.sum() > 0:
            triple /= triple.sum()
            directional = {"p_bottom_rebound_first": float(triple[0]), "p_top_reversal_first": float(triple[1]),
                "p_no_directional_turn": float(triple[2]), "directional_turn_status": "calibrated"}
            directional["directional_baseline_rates"] = directional_bundle.test_metrics.get("train_class_rates", {})
    two_way_mask = bottom_mask & top_mask
    two_way_share = event_prob(two_way_mask)
    volatility_scale = math.sqrt(max(horizon, 1) / 5.0)
    volatility_bottom_band = [max(0.01, ref - 1.25 * atr * volatility_scale), max(0.01, ref - 0.75 * atr * volatility_scale)]
    volatility_top_band = [ref + 0.75 * atr * volatility_scale, ref + 1.25 * atr * volatility_scale]
    return {
        "status": "calibrated", "opportunity_value": clean_num(opportunity), "risk_value": risk,
        "expected_return": expected, "es95": risk, "mu_lcb": mu_lcb, "effective_scenario_n": eff_n,
        "p_upfirst": float(np.sum(weights * np.array([r["label"] == "upfirst" for r in records]))),
        "p_downfirst": float(np.sum(weights * np.array([r["label"] == "downfirst" for r in records]))),
        "p_unhit": float(np.sum(weights * np.array([r["label"] == "unhit" for r in records]))),
        "p_bottom": event_prob(bottom_mask), "p_top": event_prob(top_mask),
        "stage_probability_status": "scenario_marginal_not_independently_calibrated",
        "stage_probability_definition": "share of reweighted common historical paths containing the stage event inside the horizon; not a next-session direction forecast",
        **directional,
        "directional_turn_model_version": DIRECTIONAL_MODEL_VERSION,
        "directional_turn_definition": "mutually exclusive first 1-ATR boundary followed by same-window reversal confirmation, versus no confirmed directional turn",
        "p_two_way_wash": two_way_share,
        "two_way_wash_flag": "high" if two_way_share >= 0.40 else "normal",
        "two_way_wash_status": "scenario_diagnostic_not_direction_probability",
        "two_way_wash_threshold": 0.40,
        "bottom_band": quantile_band(mins[bottom_mask], weights[bottom_mask]) if bottom_mask.any() else quantile_band(mins, weights),
        "top_band": quantile_band(maxs[top_mask], weights[top_mask]) if top_mask.any() else quantile_band(maxs, weights),
        "volatility_bottom_band": volatility_bottom_band,
        "volatility_top_band": volatility_top_band,
        "volatility_band_method": "ATR14 x sqrt(horizon/5), inner 0.75 ATR and outer 1.25 ATR; display fallback only",
        "terminal_band": quantile_band(terminal, weights),
        "terminal_p10": weighted_quantile(terminal, weights, 0.1), "terminal_p50": weighted_quantile(terminal, weights, 0.5), "terminal_p90": weighted_quantile(terminal, weights, 0.9),
        "scenario_set": "nearest-point-in-time-history-with-model-class-reweighting",
        "scenario_count": len(records), "cost_assumption": 0.0015,
        "decision_audit_version": DECISION_AUDIT_VERSION,
        "history_source": history_source,
        "peer_transfer_count": int(peer_transfer_count or 0),
        "risk_rank_eligible": bool(risk_audit["risk_metric_valid"]),
        "opportunity_rank_eligible": bool(risk_audit["risk_metric_valid"]),
        "probability_unit": "fraction_0_to_1",
        "score_unit": "cross_sectional_rank_0_to_100_not_probability",
        "directional_margin_threshold_source": "one_year_directional_turn_backtest",
        "probability_margin_threshold_backtest": margin_threshold,
        **risk_audit, **sample_audit,
        **confidence,
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


def options_model_gate(current_ranges: dict[str, dict[str, Any]], as_of: pd.Timestamp) -> dict[str, Any]:
    """Audit point-in-time option history before any option feature can fit probabilities."""
    required = ("atm_iv", "rv_minus_iv", "put_call_skew", "term_structure_vs_1w")
    dates: set[str] = set()
    feature_seen = {name: 0 for name in required}
    observed_rows = 0

    def inspect(records: list[dict[str, Any]]) -> None:
        nonlocal observed_rows
        for record in records:
            snapshot = record.get("snapshot") or record.get("potential_ranges") or {}
            for payload in (snapshot.get("horizons") or {}).values():
                option = payload.get("layer2_options_distribution") or {}
                layer3 = payload.get("layer3_skew_event") or {}
                if option.get("status") != "observed":
                    continue
                date = payload.get("as_of")
                if date:
                    dates.add(str(date))
                observed_rows += 1
                merged = {**option, **layer3}
                for name in required:
                    if clean_num(merged.get(name)) is not None:
                        feature_seen[name] += 1

    frozen_files = sorted((STATE_DIR / "frozen" / "options").glob("*.json"))
    for path in frozen_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            inspect(payload.get("records") or [])
        except (OSError, ValueError, TypeError):
            continue
    inspect([{"snapshot": value} for value in current_ranges.values()])
    dates.add(as_of.strftime("%Y-%m-%d"))
    coverage = {name: (feature_seen[name] / observed_rows if observed_rows else 0.0) for name in required}
    minimum_sessions = 252
    eligible = len(dates) >= minimum_sessions and all(value >= 0.80 for value in coverage.values())
    missing = [name for name, value in coverage.items() if value < 0.80]
    reason = ("连续点时期权样本与字段覆盖达到预注册门槛；仍需独立回测批准。" if eligible else
        f"仅有{len(dates)}个唯一点时期权交易日，要求至少{minimum_sessions}日；字段覆盖不足：{', '.join(missing) if missing else '无'}。")
    return {
        "status": "READY_FOR_BACKTEST" if eligible else "BLOCKED",
        "training_eligible": eligible,
        "candidate_model": "GBDT challenger with chronological train/calibration/test split",
        "required_features": list(required),
        "optional_unavailable_features": ["gamma_flip", "dealer_gex", "options_flow"],
        "unique_point_in_time_sessions": len(dates),
        "minimum_sessions": minimum_sessions,
        "observed_option_rows": observed_rows,
        "feature_coverage": coverage,
        "current_integration": "not_in_champion_or_directional_probability_model",
        "collection": "append-only encrypted option snapshots",
        "reason": reason,
        "note": "当前快照继续用于价带约束和尾部描述；未用今日截面回填历史，也未套用旧校准器。",
    }


def enrich_record(row: dict[str, Any], frame: pd.DataFrame, metric_by_h: dict[int, dict[str, Any]], identity: dict[str, Any], peers: dict[str, Any], as_of: pd.Timestamp, potential_ranges: dict[str, Any] | None = None) -> dict[str, Any]:
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
    potential_payload = json.loads(json.dumps(potential_ranges or {}, ensure_ascii=False))
    for h in HORIZONS:
        horizon_payload = (potential_payload.get("horizons") or {}).get(str(h), {})
        metric = metric_by_h.get(h, {})
        bottom = horizon_payload.get("candidate_bottom")
        top = horizon_payload.get("candidate_top")
        structure = horizon_payload.get("layer1_price_structure") or {}
        if isinstance(bottom, dict):
            bottom["arrival_probability"] = clean_num(metric.get("p_downfirst"))
            bottom["directional_turn_probability"] = clean_num(metric.get("p_bottom_rebound_first"))
            bottom["scenario_event_share"] = clean_num(metric.get("p_bottom"))
            bottom["structural_band"] = (structure.get("core_bottom_zone") or {}).get("band") or structure.get("support_band")
            bottom["core_zone"] = structure.get("core_bottom_zone")
            bottom["volatility_band"] = metric.get("volatility_bottom_band")
            bottom["probability_note"] = "方向性阶段底要求先触及下边界并在同窗完成反弹确认；与阶段顶、无有效拐点互斥。原共同路径底部事件占比仅作双向波动诊断。"
        if isinstance(top, dict):
            top["arrival_probability"] = clean_num(metric.get("p_upfirst"))
            top["directional_turn_probability"] = clean_num(metric.get("p_top_reversal_first"))
            top["scenario_event_share"] = clean_num(metric.get("p_top"))
            top["structural_band"] = (structure.get("core_top_zone") or {}).get("band") or structure.get("resistance_band")
            top["core_zone"] = structure.get("core_top_zone")
            top["volatility_band"] = metric.get("volatility_top_band")
            top["probability_note"] = "方向性阶段顶要求先触及上边界并在同窗完成回落确认；与阶段底、无有效拐点互斥。原共同路径顶部事件占比仅作双向波动诊断。"
        if isinstance(horizon_payload, dict):
            horizon_payload["model_path_band"] = {
                "terminal_band": metric.get("terminal_band"),
                "bottom_band": metric.get("bottom_band"),
                "top_band": metric.get("top_band"),
                "volatility_bottom_band": metric.get("volatility_bottom_band"),
                "volatility_top_band": metric.get("volatility_top_band"),
                "note": "模型共同路径条件价带；与原始统计包络、结构核心区分层展示，不替代方向性概率。",
            }
            horizon_payload["direction_probabilities"] = {
                "bottom_rebound_first": clean_num(metric.get("p_bottom_rebound_first")),
                "top_reversal_first": clean_num(metric.get("p_top_reversal_first")),
                "no_directional_turn": clean_num(metric.get("p_no_directional_turn")),
                "note": "三项互斥方向性分类严格合计100%；结构共振不进入该概率。",
            }
        if metric.get("status") in ("calibrated", "calibrated_low_confidence"):
            bottom_candidate = bottom if isinstance(bottom, dict) else {}
            top_candidate = top if isinstance(top, dict) else {}
            structure_bottom = (structure.get("core_bottom_zone") or {}).get("band") or structure.get("support_band")
            structure_top = (structure.get("core_top_zone") or {}).get("band") or structure.get("resistance_band")
            bottom_fallback = bottom_candidate.get("band") if bottom_candidate.get("status") in ("converged", "structure_only") else structure_bottom
            top_fallback = top_candidate.get("band") if top_candidate.get("status") in ("converged", "structure_only") else structure_top
            bottom_zone = _valid_band(metric.get("bottom_band")) or _valid_band(bottom_fallback)
            top_zone = _valid_band(metric.get("top_band")) or _valid_band(top_fallback)
            metric["bottom_zone"] = bottom_zone
            metric["top_zone"] = top_zone
            metric["bottom_zone_source"] = "turning_point_conditional_path" if _valid_band(metric.get("bottom_band")) else "price_structure_evidence_fallback"
            metric["top_zone_source"] = "turning_point_conditional_path" if _valid_band(metric.get("top_band")) else "price_structure_evidence_fallback"
            metric["bottom_zone_definition"] = "方向性阶段底条件路径区；不是±1σ统计包络，也不是结构共振本身。"
            metric["top_zone_definition"] = "方向性阶段顶条件路径区；不是±1σ统计包络，也不是结构共振本身。"
            metric["distance_to_bottom_zone_pct"] = _distance_to_band(clean_num(current.get("adj_close")), bottom_zone)
            metric["distance_to_top_zone_pct"] = _distance_to_band(clean_num(current.get("adj_close")), top_zone)
            metric["current_in_bottom_zone"] = bool(bottom_zone and bottom_zone[0] <= float(current["adj_close"]) <= bottom_zone[1])
            metric["current_in_top_zone"] = bool(top_zone and top_zone[0] <= float(current["adj_close"]) <= top_zone[1])
            metric["distance_to_bottom_zone_pct"] = 0.0 if metric["current_in_bottom_zone"] else metric["distance_to_bottom_zone_pct"]
            metric["distance_to_top_zone_pct"] = 0.0 if metric["current_in_top_zone"] else metric["distance_to_top_zone_pct"]
            metric["p_touch_bottom_zone"] = 1.0 if metric["current_in_bottom_zone"] else clean_num(metric.get("p_downfirst"))
            metric["p_touch_top_zone"] = 1.0 if metric["current_in_top_zone"] else clean_num(metric.get("p_upfirst"))
            metric["p_rebound_given_touch"] = (clean_num(metric.get("p_bottom_rebound_first")) / max(metric["p_touch_bottom_zone"], 1e-9)) if metric.get("p_touch_bottom_zone") else None
            metric["p_reversal_given_touch"] = (clean_num(metric.get("p_top_reversal_first")) / max(metric["p_touch_top_zone"], 1e-9)) if metric.get("p_touch_top_zone") else None
            metric["p_touch_and_rebound"] = clean_num(metric.get("p_bottom_rebound_first"))
            metric["p_touch_and_reversal"] = clean_num(metric.get("p_top_reversal_first"))
            metric["joint_event_definition"] = "方向性模型直接输出的先触达且同窗反转联合事件；条件概率仅在该联合事件为触达事件子集时展示。"
            metric.update(_decision_fields(metric, current, metric.get("probability_margin_threshold_backtest")))
            metric["candidate_zone_status"] = {"bottom": "当前已进入候选底部区" if metric["current_in_bottom_zone"] else "候选底部区尚未进入", "top": "当前已进入候选顶部区" if metric["current_in_top_zone"] else "候选顶部区尚未进入"}
    return {**row, "as_of": as_of.strftime("%Y-%m-%d"), "reference_price": clean_num(current["adj_close"]), "metrics": safe_json(metrics), "stage": stage, "issuer_id": identity.get("issuer_id"), "issuer_identity_source": identity.get("source"), "peer_context": safe_json({**peers, "note": peer_note, "issuer_id": identity.get("issuer_id"), "subgroup_context": peers.get("subgroup_context", {})}), "potential_ranges": safe_json(potential_payload), "rotation_explanation": "真实日线数据已接入；市场、研究组与个股残差分别计算，允许不同步。" + (" 存储细分与LOO为影子候选，未进入正式校准分数。" if storage else ""), "trigger_summary": "确认：先由Price Structure形成候选位；只有先触边界且同窗满足反转条件才计入互斥方向性阶段顶/底。失效：跳空、事件冲击或重新突破结构。到达概率、方向性拐点、双向洗盘诊断和交易成功不可混同。结构共振只用于解释核心区，不进入概率。", "event_summary": "本次生产构建未抓取并公开长文本财报、产品发布或宏观事件正文；事件特征为缺失，不把标题或业务分类当作催化概率。", "risk_summary": "风险值来自共同历史路径的最差5%不利幅度均值；风险指标另有合法性审计，异常原值保留但不进入风险榜。", "data_note": f"数据源：{SOURCE_NAME}；截止{as_of.strftime('%Y-%m-%d')}。统计机会/风险榜与方向决策榜分离；方向性阶段底、阶段顶、无有效拐点由独立时间校准的三分类挑战者生成并严格归一。重叠顶底事件仅作双向洗盘诊断。统计/结构/模型路径层不互相伪造概率；期权历史不足，尚未进入概率模型。"}


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
    if run_id and (OUTPUT_DIR / f"dashboard-{run_id}.json").exists():
        raise ValueError("run_id already frozen; use a new run_id for a revision")
    print("[radar] loading seed and price snapshot", flush=True)
    seeds = read_seed()
    seed_symbols = [r["symbol"] for r in seeds]
    extra_symbol = extra_symbol.upper().replace(".", "-") if extra_symbol else None
    symbols = list(dict.fromkeys(seed_symbols + ([extra_symbol] if extra_symbol else []) + list(MARKET_SYMBOLS)))
    frames, source = download_prices(symbols, refresh=refresh)
    print(f"[radar] prices ready: {len(frames)}/{len(symbols)}", flush=True)
    if "SPY" not in frames:
        raise RuntimeError("SPY market benchmark unavailable; cannot build a live radar")
    # Use the intersection date already filtered to a completed New York
    # session, rather than SPY's own latest row (which may be an intraday bar).
    as_of = pd.Timestamp(source["common_latest_date"]) if source.get("common_latest_date") else max(frames["SPY"].index)
    model_frames = {symbol: frame.loc[:as_of].copy() for symbol, frame in frames.items()}
    sec = fetch_sec_identities(seed_symbols)
    groups = {r.get("research_group", "") for r in seeds}
    group_series = {g: build_group_series(model_frames, seeds, g) for g in groups}
    market = model_frames["SPY"]["adj_close"]
    samples, feature_frames = make_samples(model_frames, seeds, market, group_series, storage_features=False)
    print("[radar] point-in-time labels ready", flush=True)
    bundles: dict[int, ModelBundle | None] = {}
    directional_bundles: dict[int, ModelBundle | None] = {}
    backtest: dict[str, Any] = {"model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION, "strategy_version": STRATEGY_VERSION, "horizons": {}, "storage_incremental": {"status": "BLOCKED", "reason": "taxonomy effective_from=2026-09-15 leaves no mature point-in-time storage membership window for a same-window sample-out test; challenger is shadow-only."}}
    for h in HORIZONS:
        b2 = train_bundle(samples[h], TECHNICAL_FEATURES, h)
        bundle = train_bundle(samples[h], BASE_FEATURES, h)
        directional_bundle = train_bundle(samples[h], BASE_FEATURES, h, target_col="turn_label")
        bundles[h] = bundle
        directional_bundles[h] = directional_bundle
        backtest["horizons"][str(h)] = {"b2_technical": b2.test_metrics if b2 else {"status": "insufficient_training_data"},
            "b3_market_rotation": bundle.test_metrics if bundle else {"status": "insufficient_training_data"},
            "directional_turn_exclusive": directional_bundle.test_metrics if directional_bundle else {"status": "insufficient_training_data"}}
        print(f"[radar] model {h}d ready", flush=True)
    backtest["one_year_walk_forward"] = one_year_walk_forward_backtest(samples)
    backtest["one_year_directional_turn"] = one_year_directional_turn_backtest(samples)
    decision_margin_thresholds = {
        h: clean_num((backtest.get("one_year_directional_turn", {}).get("horizons", {}).get(str(h), {}) or {}).get("decision_margin_threshold"))
        for h in HORIZONS
    }
    print("[radar] one-year walk-forward backtest ready", flush=True)
    potential_symbols = list(dict.fromkeys(seed_symbols + ([extra_symbol] if extra_symbol else [])))
    potential_ranges, potential_source = build_potential_ranges(frames, potential_symbols, as_of)
    source["potential_ranges"] = potential_source
    option_gate = options_model_gate(potential_ranges, as_of)
    backtest["options_b4"] = option_gate
    print(f"[radar] three-layer potential ranges ready: {potential_source.get('observed_symbols', 0)}/{potential_source.get('requested_symbols', 0)} option snapshots", flush=True)
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
            metric_by_h[h] = build_scenario_metric(symbol, current, hist, bundles[h], h, frame,
                directional_bundle=directional_bundles[h], history_source="own_history",
                margin_threshold=decision_margin_thresholds.get(h))
            latest_metrics[h][symbol] = metric_by_h[h].get("opportunity_value")
        peers = {"status": "unavailable", "self_excluded": False, "peer_symbols": [], "peer_count": 0, "effective_n": None, "weight_coverage": None, "note": "非存储股票不计算存储同行。"}
        if row.get("research_group_id") == "storage-memory":
            loo, peer_meta = build_storage_loo_series(model_frames, seeds, symbol)
            identity_ok = bool(sec.get(symbol, {}).get("issuer_id"))
            peers = {**peer_meta, "status": peer_meta.get("status") if identity_ok else "identity_unverified", "self_excluded": identity_ok, "issuer_id": sec.get(symbol, {}).get("issuer_id"), "subgroup_context": {}}
            for tag in ("DRAM", "NAND", "HDD"):
                _, sm = build_storage_loo_series(model_frames, seeds, symbol, tag=tag)
                peers["subgroup_context"][tag] = sm
        records.append(enrich_record(row, frame, metric_by_h, sec.get(symbol, {}), peers, as_of, potential_ranges.get(symbol)))
        if len(records) % 10 == 0:
            print(f"[radar] records {len(records)}/100", flush=True)
    for h in HORIZONS:
        opp_values = {s: (clean_num(next((r["metrics"].get(str(h), {}).get("opportunity_value") for r in records if r["symbol"] == s), None)) if bool(next((r["metrics"].get(str(h), {}).get("opportunity_rank_eligible") for r in records if r["symbol"] == s), False)) else None) for s in latest_metrics[h]}
        opp = pct_rank(opp_values)
        risk_vals = {s: (clean_num(next((r["metrics"].get(str(h), {}).get("risk_value") for r in records if r["symbol"] == s), None)) if bool(next((r["metrics"].get(str(h), {}).get("risk_rank_eligible") for r in records if r["symbol"] == s), False)) else None) for s in latest_metrics[h]}
        risk = pct_rank(risk_vals)
        for rec in records:
            m = rec["metrics"].get(str(h), {})
            if m.get("status") in ("calibrated", "calibrated_low_confidence"):
                m["opportunity_score"] = opp.get(rec["symbol"])
                m["risk_score"] = risk.get(rec["symbol"])
                m["positive_edge"] = bool((m.get("mu_lcb") or 0) > 0)
                m["opportunity_score_unit"] = "/100横截面分，不是百分比或概率"
                m["risk_score_unit"] = "/100横截面分，不是百分比或概率"
                m["opportunity_ranking_definition"] = "仅在risk_metric_valid=true时，按全体常态股票同期限opportunity_value横截面排序"
                m["risk_ranking_definition"] = "仅在risk_metric_valid=true时，按全体常态股票同期限risk_value横截面排序"
                if m.get("opportunity_score") is None:
                    m["opportunity_rank_excluded_reason"] = "risk_metric_invalid_or_data_quality_failure"
                if m.get("risk_score") is None:
                    m["risk_rank_excluded_reason"] = "risk_metric_invalid_or_data_quality_failure"
    temporary: list[dict[str, Any]] = []
    if extra_symbol and extra_symbol in frames:
        extra_row = {"symbol": extra_symbol, "name_zh": "临时观察", "coverage_bucket": "临时观察", "research_group": "未归类", "research_group_id": "temporary", "legacy_research_group": "", "business_tags": [], "industry_tags": [], "taxonomy_version": TAXONOMY_VERSION, "taxonomy_effective_from": "2026-09-15", "metadata_as_of": as_of.strftime("%Y-%m-%d")}
        raw_extra = add_features(model_frames[extra_symbol], market, pd.Series(dtype=float), None)
        current_extra = raw_extra.loc[as_of] if as_of in raw_extra.index else raw_extra.iloc[-1]
        extra_metrics: dict[int, dict[str, Any]] = {}
        for h in HORIZONS:
            extra_hist = samples[h].copy()
            extra_metrics[h] = build_scenario_metric(extra_symbol, current_extra, extra_hist, bundles[h], h, raw_extra,
                directional_bundle=directional_bundles[h], history_source="peer_transfer",
                peer_transfer_count=len(extra_hist), margin_threshold=decision_margin_thresholds.get(h))
        extra_peers = {"status": "unavailable", "self_excluded": False, "peer_symbols": [], "peer_count": 0, "effective_n": None, "weight_coverage": None, "note": "临时股票未被强行归入存储或其他研究组；结果与常态100池分开保存。"}
        temporary.append(enrich_record(extra_row, raw_extra, extra_metrics, {"issuer_id": sec.get(extra_symbol, {}).get("issuer_id"), "source": sec.get(extra_symbol, {}).get("source", "unverified")}, extra_peers, as_of, potential_ranges.get(extra_symbol)))
    decision_board: dict[str, Any] = {"version": DECISION_AUDIT_VERSION, "ranking_basis": "directional_calibrated_probability + baseline_edge + candidate_zone_distance + joint_event_probability + confidence/data_quality; opportunity_value is not a primary key", "horizons": {}}
    for h in HORIZONS:
        bottom_items: list[dict[str, Any]] = []; top_items: list[dict[str, Any]] = []
        for rec in records:
            metric = rec.get("metrics", {}).get(str(h), {})
            if not metric.get("decision_eligible"):
                continue
            base = {
                "symbol": rec["symbol"], "name_zh": rec.get("name_zh", rec["symbol"]),
                "horizon": h, "stage_primary_judgment": metric.get("stage_primary_judgment"),
                "decision_eligible": True, "confidence_score": metric.get("confidence_score"),
                "probability_margin_top1_top2": metric.get("probability_margin_top1_top2"),
                "candidate_zone_status": metric.get("candidate_zone_status"),
            }
            bprob = clean_num(metric.get("p_bottom_rebound_first")); tprob = clean_num(metric.get("p_top_reversal_first"))
            bedge = clean_num((metric.get("directional_edge_vs_baseline") or {}).get("bottom_reversal_candidate")); tedge = clean_num((metric.get("directional_edge_vs_baseline") or {}).get("top_reversal_warning"))
            if bprob is not None:
                bottom_items.append({**base, "side": "stage-bottom", "directional_probability": bprob, "directional_edge_vs_baseline": bedge, "distance_to_zone_pct": metric.get("distance_to_bottom_zone_pct"), "joint_probability": metric.get("p_touch_and_rebound"), "conditional_reversal": metric.get("p_rebound_given_touch"), "zone": metric.get("bottom_zone")})
            if tprob is not None:
                top_items.append({**base, "side": "stage-top", "directional_probability": tprob, "directional_edge_vs_baseline": tedge, "distance_to_zone_pct": metric.get("distance_to_top_zone_pct"), "joint_probability": metric.get("p_touch_and_reversal"), "conditional_reversal": metric.get("p_reversal_given_touch"), "zone": metric.get("top_zone")})
        bottom_items.sort(key=lambda x: (x.get("directional_probability") or -1, x.get("directional_edge_vs_baseline") or -1, x.get("joint_probability") or -1, x.get("confidence_score") or -1, -(x.get("distance_to_zone_pct") or 999)), reverse=True)
        top_items.sort(key=lambda x: (x.get("directional_probability") or -1, x.get("directional_edge_vs_baseline") or -1, x.get("joint_probability") or -1, x.get("confidence_score") or -1, -(x.get("distance_to_zone_pct") or 999)), reverse=True)
        for rank, item in enumerate(bottom_items, 1): item["decision_rank"] = rank
        for rank, item in enumerate(top_items, 1): item["decision_rank"] = rank
        decision_board["horizons"][str(h)] = {"stage_bottom_candidates": bottom_items, "stage_top_warnings": top_items, "note": "两榜独立，单一股票可同时出现；排序不以机会分优先。"}
    run_id = run_id or f"radar-{as_of.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    rotation = storage_rotation(model_frames, seeds, as_of)
    print("[radar] storage rotation ready", flush=True)
    group_rotation = research_group_rotation(model_frames, seeds, as_of)
    print("[radar] research-group rotation ready", flush=True)
    valid = sum(1 for r in records if any(r["metrics"].get(str(h), {}).get("status") in ("calibrated", "calibrated_low_confidence") for h in HORIZONS))
    weekly_audit: dict[str, Any] | None = None
    weekly_audit_file = ROOT / "data" / "weekly_pool_audit.json"
    if weekly_audit_file.exists():
        try:
            candidate = json.loads(weekly_audit_file.read_text(encoding="utf-8"))
            if candidate.get("as_of") and candidate["as_of"] <= as_of.strftime("%Y-%m-%d"):
                weekly_audit = candidate
        except (OSError, json.JSONDecodeError, TypeError):
            weekly_audit = None
    mother_file = Path(r"D:\codex\us-share-daily-market-html\outputs\us_share_technical_screener\2026-09-13\all_metrics.csv")
    mother_count = 0
    if mother_file.exists():
        with mother_file.open(encoding="utf-8-sig", newline="") as fh:
            mother_count = max(0, sum(1 for _ in fh) - 1)
    if weekly_audit:
        mother_count = int(weekly_audit.get("mother_pool_observed_count") or mother_count)
    source["mother_pool"] = {
        "source": "read-only legacy screener snapshot via committed weekly audit" if weekly_audit else "us-share-daily-market-html/all_metrics.csv",
        "available_symbols": mother_count,
        "qualification_verified": bool(weekly_audit and weekly_audit.get("complete_popularity_security_count", 0) >= 300),
        "automatic_reselection_status": weekly_audit.get("automatic_reselection_status") if weekly_audit else "BLOCKED",
        "note": "点时资格与人气评分已审计；自动换池仍受独立门槛约束。" if weekly_audit else "母池规模来自旧美股行情项目快照；本项目未把它改写成当前人气排名。",
    }
    data = {"build_mode": "live", "status_message": "真实日线行情已接入。机会/风险仍由原B3共同路径生成；方向性阶段底、阶段顶与无有效拐点为互斥且独立校准的挑战者，三项严格合计100%。信度按历史路径重合、近期独立测试校准、相对多数类基线增益与真实日期支持分级；信度不改写概率。详情按四层展示实现波动率统计包络、结构核心区、模型路径区和方向概率；结构/统计/期权显示层不反推概率，事件未接入时显式标注。", "generated_at": iso(utc_now()), "as_of": as_of.strftime("%Y-%m-%d"), "as_of_beijing": f"{as_of.strftime('%Y-%m-%d')} 纽约收盘数据；北京时间日期需按交易日换算", "run_id": run_id, "prediction_snapshot_id": run_id, "model_version": MODEL_VERSION, "directional_model_version": DIRECTIONAL_MODEL_VERSION, "confidence_method_version": CONFIDENCE_METHOD_VERSION, "feature_version": FEATURE_VERSION, "potential_range_version": "structure-statistical-model-option-four-layer-v4", "strategy_version": STRATEGY_VERSION, "universe_version": "curated-seed-20260915-v1.1-live-validation", "taxonomy_version": TAXONOMY_VERSION, "source_manifest": source, "records": records, "temporary": temporary, "storage_rotation": rotation, "research_group_rotation": group_rotation, "weekly_pool_audit": weekly_audit, "options_model_gate": option_gate, "rotation_summary": group_rotation["summary"], "public_config": {"api_base_url": None}, "backtest": backtest, "coverage": {"regular_pool": len(records), "valid_forecast_records": valid, "usable_price_records": sum(1 for r in records if r.get("reference_price") is not None), "data_cutoff": as_of.strftime("%Y-%m-%d"), "financial_backtest_status": "B3 first-touch and exclusive directional-turn time-split metrics computed; options B4 and storage incremental alpha remain gated"}}
    data["decision_board"] = decision_board
    data["status_message"] = "真实日线行情已接入。统计机会/风险榜与方向决策榜分离；风险单位异常保留原值但不进入风险榜。方向性阶段底、阶段顶与无有效拐点为互斥且独立校准的挑战者，三项严格合计100%。信度、ESS、数据质量和校准门共同决定决策资格，不改写原始概率。详情按统计包络、结构核心区、模型路径区和候选联合事件展示；事件未接入时显式标注。"
    # Context benchmarks cannot move the champion's stock/market cutoff date.
    index_frames, index_source = download_prices([s for s in INDEX_SPECS if s not in frames], refresh=refresh)
    context_frames = {**frames, **index_frames}
    data["index_context"] = build_index_context(context_frames, records + temporary, as_of)
    try:
        from index_forecasts import build_index_forecasts
    except ModuleNotFoundError:
        from scripts.index_forecasts import build_index_forecasts
    data['index_forecasts'] = build_index_forecasts(sys.modules[__name__], context_frames, data['index_context'], as_of)
    try:
        from boundary_alerts import build_boundary_breach_alerts
    except ModuleNotFoundError:
        from scripts.boundary_alerts import build_boundary_breach_alerts
    data['boundary_breach_alerts'] = build_boundary_breach_alerts(
        records,
        data['index_forecasts']['records'],
        data['as_of'],
        run_id,
        STATE_DIR / 'frozen' / 'predictions',
    )
    backtest['index_targets'] = {r['symbol']:r['validation'] for r in data['index_forecasts']['records']}
    source["index_context"] = index_source
    write_json(OUTPUT_DIR / f"dashboard-{run_id}.json", data)
    write_json(OUTPUT_DIR / f"backtest-{run_id}.json", backtest)
    write_json(STATE_DIR / "model_card.json", {"model_version": MODEL_VERSION, "directional_model_version": DIRECTIONAL_MODEL_VERSION, "confidence_method_version": CONFIDENCE_METHOD_VERSION, "feature_version": FEATURE_VERSION, "calibration": "first-touch classes and exclusive directional-turn classes use independent chronological calibration; overlapping stage-event marginals are diagnostic scenario shares only", "status": "calibrated_confidence_tiered", "champion": "B3 opportunity/risk without storage or option challenger", "challenger": "exclusive directional turn retains challenger status; evidence confidence is tiered per asset/horizon and does not imply promotion; storage LOO/subgroup and options B4 remain gated", "backtest": backtest, "source": source})
    db = ensure_ledger()
    db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (run_id, iso(utc_now()), data["as_of"], MODEL_VERSION, FEATURE_VERSION, "succeeded", str(OUTPUT_DIR / f"dashboard-{run_id}.json")))
    for rec in records + data['index_forecasts']['records']:
        for h in HORIZONS:
            db.execute("INSERT INTO predictions VALUES (?,?,?,?,?,?)", (f"{run_id}:{rec['symbol']}:{h}", run_id, rec["symbol"], h, rec["as_of"], json.dumps(rec["metrics"][str(h)], ensure_ascii=False),))
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
