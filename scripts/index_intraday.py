"""Build a three-index intraday price/volume/options map.

This module is deliberately separate from the calibrated stock turning-point
model.  It uses the cash index for point quotes and an explicitly named ETF
proxy for executed-volume structure.  The resulting snapshot is explanatory
context only and never changes stock or index probabilities.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import tempfile
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state" / "index_intraday"
NY = ZoneInfo("America/New_York")
BJT = ZoneInfo("Asia/Shanghai")
SESSION_START = time(9, 30)
SESSION_END = time(16, 0)
ALGORITHM_VERSION = "volume-profile-uniform-ohlcv-v1.0.0"
SCHEMA_VERSION = "index-intraday-map-v1"

SPECS: tuple[dict[str, Any], ...] = (
    {"key": "nasdaq", "index_symbol": "^IXIC", "index_name": "纳斯达克综合指数", "proxy": "QQQ", "proxy_name": "QQQ", "proxy_role": "纳指100交易结构代理", "options": ["QQQ"], "mapping": "QQQ→NDX only; 禁止QQQ→IXIC机械换算"},
    {"key": "sp500", "index_symbol": "^GSPC", "index_name": "标普500指数", "proxy": "SPY", "proxy_name": "SPY", "proxy_role": "标普500交易结构代理", "options": ["SPY", "SPX"], "mapping": "SPY→SPX同目标时点映射参考"},
    {"key": "dow", "index_symbol": "^DJI", "index_name": "道琼斯工业平均指数", "proxy": "DIA", "proxy_name": "DIA", "proxy_role": "道指交易结构代理", "options": ["DIA"], "mapping": "DIA→DJIA同目标时点映射参考"},
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        return value.isoformat()
    return str(value)


def _num(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _clean_frame(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(c[0]).lower() for c in frame.columns]
    frame.columns = [str(c).lower().replace(" ", "_") for c in frame.columns]
    if "adj_close" in frame.columns and "close" in frame.columns:
        frame = frame.drop(columns=["adj_close"])
    else:
        frame = frame.rename(columns={"adj_close": "close"})
    for col in ("open", "high", "low", "close", "volume"):
        if col not in frame:
            frame[col] = np.nan if col != "volume" else 0.0
    idx = pd.to_datetime(frame.index)
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    frame.index = idx.tz_convert(NY)
    frame = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["close"]).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame["volume"] = frame["volume"].fillna(0.0).clip(lower=0.0)
    return frame


def _regular(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    idx = frame.index
    mask = (idx.time >= SESSION_START) & (idx.time < SESSION_END)
    return frame.loc[mask].copy()


def _sessions(frame: pd.DataFrame) -> list[date]:
    return sorted({x.date() for x in frame.index})


def _profile(frame: pd.DataFrame, window_name: str, start: date | None, end: date | None, tick: float = 0.01) -> dict[str, Any]:
    subset = frame
    if start is not None:
        subset = subset[subset.index.date >= start]
    if end is not None:
        subset = subset[subset.index.date <= end]
    subset = subset.dropna(subset=["high", "low", "close"])
    volume_total = float(subset.volume.sum()) if not subset.empty else 0.0
    if subset.empty or volume_total <= 0:
        return {"status": "unavailable", "window": window_name, "window_start": str(start) if start else None, "window_end": str(end) if end else None, "reason": "no_completed_ohlcv_volume", "bins": []}
    lo = float(subset.low.min())
    hi = float(subset.high.max())
    raw_width = max((hi - lo) / 80.0, tick)
    width = max(tick, math.ceil(raw_width / tick) * tick)
    origin = math.floor(lo / width) * width
    n = max(1, int(math.ceil((hi - origin) / width)))
    volumes = np.zeros(n, dtype=float)
    for row in subset.itertuples():
        low, high, vol = float(row.low), float(row.high), float(row.volume)
        if not math.isfinite(vol) or vol <= 0:
            continue
        if not math.isfinite(low) or not math.isfinite(high):
            continue
        if high < low:
            low, high = high, low
        if high == low:
            i = min(n - 1, max(0, int(math.floor((low - origin) / width))))
            volumes[i] += vol
            continue
        first = max(0, int(math.floor((low - origin) / width)))
        last = min(n - 1, int(math.floor((high - origin) / width)))
        span = high - low
        for i in range(first, last + 1):
            a, b = origin + i * width, origin + (i + 1) * width
            overlap = max(0.0, min(high, b) - max(low, a))
            if overlap:
                volumes[i] += vol * overlap / span
    if volumes.sum() <= 0:
        return {"status": "unavailable", "window": window_name, "window_start": str(start) if start else None, "window_end": str(end) if end else None, "reason": "volume_bins_empty", "bins": []}
    poc_i = int(np.argmax(volumes))
    target = volume_total * 0.70
    included = float(volumes[poc_i])
    left, right = poc_i, poc_i
    while included < target and (left > 0 or right < n - 1):
        lv = volumes[left - 1] if left > 0 else -1.0
        rv = volumes[right + 1] if right < n - 1 else -1.0
        if rv > lv or (rv == lv and right < n - 1):
            right += 1; included += float(volumes[right])
        else:
            left -= 1; included += float(volumes[left])
    bins = []
    max_volume = float(volumes.max()) or 1.0
    for i, value in enumerate(volumes):
        bins.append({"low": round(origin + i * width, 6), "high": round(origin + (i + 1) * width, 6), "volume": round(float(value), 6), "relative_volume": round(float(value / max_volume), 6), "is_poc": i == poc_i, "in_value_area": left <= i <= right})
    # Fixed local peak/trough rule; a node is only named when it is not an edge.
    hvn, lvn = [], []
    for i in range(1, n - 1):
        if volumes[i] >= volumes[i - 1] and volumes[i] >= volumes[i + 1] and volumes[i] >= max_volume * 0.60:
            hvn.append({"low": bins[i]["low"], "high": bins[i]["high"], "relative_volume": bins[i]["relative_volume"]})
        if volumes[i] <= volumes[i - 1] and volumes[i] <= volumes[i + 1] and volumes[i] <= max_volume * 0.25:
            lvn.append({"low": bins[i]["low"], "high": bins[i]["high"], "relative_volume": bins[i]["relative_volume"]})
    typical = (subset.high + subset.low + subset.close) / 3.0
    vwap = float((typical * subset.volume).sum() / volume_total)
    return {"status": "observed", "window": window_name, "window_start": str(start) if start else None, "window_end": str(end) if end else None, "bar_interval": "5m", "bin_width": round(width, 6), "bin_origin": round(origin, 6), "volume_unit": "proxy_shares", "volume_allocation": "OHLCV高低区间按重叠长度均匀分配；H=L全部归入该箱", "volume_total": round(volume_total, 6), "value_area_target": 0.70, "value_area_actual": round(float(included / volumes.sum()), 6), "poc": round((origin + (poc_i + 0.5) * width), 6), "val": bins[left]["low"], "vah": bins[right]["high"], "vwap": round(vwap, 6), "hvn": hvn[:5], "lvn": lvn[:5], "bins": bins, "included_bar_count": int(len(subset)), "missing_volume_bar_count": int((subset.volume <= 0).sum())}


def _latest_quote(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"status": "unavailable", "price": None, "as_of": None, "change_pct": None}
    last = frame.iloc[-1]
    prior = frame.iloc[-2] if len(frame) > 1 else None
    price = _num(last.close)
    prev = _num(prior.close) if prior is not None else None
    return {"status": "observed", "price": price, "as_of": _iso(frame.index[-1]), "change_pct": ((price / prev) - 1) if price and prev else None}


def _options(symbols: list[str], as_of: str | None) -> dict[str, Any]:
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            expiries = list(ticker.options or [])
            if not expiries:
                continue
            expiries = expiries[:4]
            rows = []
            for expiry in expiries:
                chain = ticker.option_chain(expiry)
                for side, frame in (("call", chain.calls), ("put", chain.puts)):
                    if frame is None or frame.empty:
                        continue
                    frame = frame.copy()
                    frame["side"] = side
                    frame["expiry"] = expiry
                    rows.append(frame)
            if not rows:
                continue
            all_rows = pd.concat(rows, ignore_index=True)
            for column in ("openInterest", "volume"):
                if column not in all_rows.columns:
                    all_rows[column] = 0.0
            all_rows["openInterest"] = pd.to_numeric(all_rows.get("openInterest"), errors="coerce").fillna(0)
            all_rows["volume"] = pd.to_numeric(all_rows.get("volume"), errors="coerce").fillna(0)
            oi = all_rows.groupby(["side", "strike"], as_index=False)["openInterest"].sum()
            vol = all_rows.groupby(["side", "strike"], as_index=False)["volume"].sum()
            def top(group: pd.DataFrame, field: str) -> list[dict[str, Any]]:
                out = group[group[field] > 0].sort_values(field, ascending=False).head(5)
                return [{"strike": _num(row.get("strike")), "value": _num(row.get(field))} for _, row in out.iterrows()]
            call_oi, put_oi = oi[oi.side == "call"], oi[oi.side == "put"]
            call_vol, put_vol = vol[vol.side == "call"], vol[vol.side == "put"]
            atm = all_rows["impliedVolatility"] if "impliedVolatility" in all_rows else pd.Series(dtype=float)
            iv = _num(pd.to_numeric(atm, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().median())
            return {"status": "observed", "underlying": symbol, "expiry_dates": expiries, "oi_as_of_date": None, "oi_as_of_note": "Yahoo期权链未提供可验证结算日；OI按供应商链快照展示，不等同盘中新仓", "options_quote_as_of": as_of, "call_oi_dense": top(call_oi, "openInterest"), "put_oi_dense": top(put_oi, "openInterest"), "call_volume_dense": top(call_vol, "volume"), "put_volume_dense": top(put_vol, "volume"), "put_call_oi_ratio": _num(put_oi.openInterest.sum() / call_oi.openInterest.sum()) if call_oi.openInterest.sum() else None, "put_call_volume_ratio": _num(put_vol.volume.sum() / call_vol.volume.sum()) if call_vol.volume.sum() else None, "atm_iv": iv, "gamma_status": "unavailable_greeks_not_verified", "max_pain_status": "not_computed", "coverage": {"expiry_count": len(expiries), "row_count": int(len(all_rows))}}
        except Exception as exc:
            last_error = str(exc)
    return {"status": "unavailable", "reason": "option_chain_unavailable_or_rate_limited", "detail": locals().get("last_error", "no_expiry_or_chain"), "underlying": None, "options_quote_as_of": as_of, "oi_as_of_date": None, "gamma_status": "unavailable"}


def _mapping(index_quote: dict[str, Any], proxy_quote: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    ratio = None
    if index_quote.get("price") and proxy_quote.get("price") and spec["key"] != "nasdaq":
        ratio = index_quote["price"] / proxy_quote["price"]
    return {"method": spec["mapping"], "allowed": spec["key"] != "nasdaq" or False, "ratio": ratio, "index_price_as_of": index_quote.get("as_of"), "proxy_price_as_of": proxy_quote.get("as_of"), "error_sources": "跨源时差、ETF基差、公司行动与指数非成交性质；不把代理POC改写为现金指数真实成交价"}


def _judgment(proxy_quote: dict[str, Any], today: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    price = proxy_quote.get("price")
    if today.get("status") != "observed" or price is None:
        return {"state": "evidence_insufficient", "label": "证据不足", "summary": "分钟成交量或完整窗口不足，不能判断成交重心。", "upside": None, "downside": None, "invalidation": "获得完整代理成交数据后重算"}
    poc, val, vah, vwap = today.get("poc"), today.get("val"), today.get("vah"), today.get("vwap")
    shift = None
    if previous.get("status") == "observed" and poc and previous.get("poc"):
        shift = poc - previous["poc"]
    if price > vah and shift is not None and shift > 0:
        state, label = "strong_continuation", "偏强延续"
    elif price < val and shift is not None and shift < 0:
        state, label = "weak_continuation", "偏弱延续"
    elif val <= price <= vah:
        state, label = "range", "区间震荡"
    elif price > vah:
        state, label = "breakout_pending", "突破待确认"
    else:
        state, label = "breakdown_pending", "跌破待确认"
    def scenario(up: bool) -> dict[str, Any]:
        trigger = vah if up else val
        first = (today.get("hvn") or [{}])[0].get("high" if up else "low") if today.get("hvn") else None
        second = (today.get("hvn") or [{}, {}])[1].get("high" if up else "low") if len(today.get("hvn") or []) > 1 else None
        return {"reference_price": trigger, "unit": "proxy_usd", "timeframe": "完成的两根5分钟K线", "confirmation": f"连续两根已完成5分钟K线{'收在' if up else '收在'} {trigger:.2f}{'上方' if up else '下方'}，并有可用成交量支持" if trigger else "参考位不可用", "first_attention": first, "second_attention": second, "invalidation": f"重新收回{'VAH下方' if up else 'VAL上方'}并回到价值区", "valid_until": "下一次盘中快照或常规时段收盘"}
    return {"state": state, "label": label, "summary": f"代理现价 {price:.2f}；POC {poc:.2f}，VWAP {vwap:.2f}；成交重心相对前一窗口{'上移' if (shift or 0) > 0 else '下移' if (shift or 0) < 0 else '未变/缺失'}。", "upside": scenario(True), "downside": scenario(False), "invalidation": "两根已完成5分钟K线确认相反方向后失效", "poc_shift": shift}


def _unavailable(spec: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION, "status": "unavailable", "instrument": {"key": spec["key"], "index_symbol": spec["index_symbol"], "index_name": spec["index_name"], "proxy_symbol": spec["proxy"], "proxy_name": spec["proxy_name"], "proxy_role": spec["proxy_role"]}, "underlying": spec["proxy"], "price_unit": {"index": "index_points", "proxy": "USD", "options": "USD_strike"}, "reason": reason, "index_quote": {"status": "unavailable"}, "proxy_quote": {"status": "unavailable"}, "quote_as_of": {"index": None, "proxy": None}, "profile_as_of": None, "options_quote_as_of": None, "oi_as_of_date": None, "delay_seconds": None, "proxy_mapping": {"method": spec["mapping"], "allowed": False}, "windows": {}, "options": {"status": "unavailable", "reason": reason}, "judgment": {"state": "evidence_insufficient", "label": "证据不足", "summary": reason}, "quality": {"status": "blocked", "missing_reason": reason}, "quality_status": "blocked", "missing_reason": reason, "coverage": None, "daily_model_as_of": None}


def _stale_copy(previous: dict[str, Any], reason: str) -> dict[str, Any]:
    stale = copy.deepcopy(previous)
    stale["status"] = "observed"
    stale["refresh_status"] = "failed_using_previous_snapshot"
    stale["missing_reason"] = reason
    stale["quality_status"] = "stale"
    stale.setdefault("quality", {})["status"] = "stale"
    stale["quality"]["missing_reason"] = reason
    stale["generated_at"] = datetime.now(timezone.utc).isoformat()
    return stale


def build_card(spec: dict[str, Any], refresh: bool = True) -> dict[str, Any]:
    try:
        previous_card = None
        latest_path = STATE_DIR / "latest.json"
        if latest_path.exists():
            try:
                prior = json.loads(latest_path.read_text(encoding="utf-8"))
                previous_card = next((x for x in prior.get("cards", []) if x.get("instrument", {}).get("key") == spec["key"]), None)
            except (OSError, json.JSONDecodeError, TypeError):
                previous_card = None
        index_frame = _regular(_clean_frame(yf.Ticker(spec["index_symbol"]).history(period="5d", interval="5m", auto_adjust=False, prepost=False)))
        proxy_frame = _regular(_clean_frame(yf.Ticker(spec["proxy"]).history(period="5d", interval="5m", auto_adjust=False, prepost=False)))
        if proxy_frame.empty:
            if previous_card:
                return _stale_copy(previous_card, "代理分钟数据本次刷新失败，沿用最近成功快照")
            return _unavailable(spec, "代理5分钟OHLCV不可用")
        sessions = _sessions(proxy_frame)
        if not sessions:
            if previous_card:
                return _stale_copy(previous_card, "代理本次没有可用常规交易时段数据，沿用最近成功快照")
            return _unavailable(spec, "代理无常规交易时段数据")
        latest, previous = sessions[-1], sessions[-2] if len(sessions) > 1 else sessions[-1]
        today = _profile(proxy_frame, "当日常规时段（盘中形成中/收盘后冻结）", latest, latest)
        prior = _profile(proxy_frame, "前一完整交易日常规时段（冻结参考）", previous, previous)
        composite_start = sessions[max(0, len(sessions) - 5)]
        composite = _profile(proxy_frame, "最近5个交易时段复合", composite_start, latest)
        index_quote, proxy_quote = _latest_quote(index_frame), _latest_quote(proxy_frame)
        if index_quote.get("status") == "unavailable" and previous_card and previous_card.get("index_quote", {}).get("price") is not None:
            index_quote = copy.deepcopy(previous_card["index_quote"])
            index_quote["status"] = "stale"
        if index_quote.get("as_of") and proxy_quote.get("as_of"):
            # Keep the index quote and proxy quote separate; only same-target mappings are allowed.
            pass
        card = {"schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION, "status": "observed", "instrument": {"key": spec["key"], "index_symbol": spec["index_symbol"], "index_name": spec["index_name"], "proxy_symbol": spec["proxy"], "proxy_name": spec["proxy_name"], "proxy_role": spec["proxy_role"], "index_unit": "index_points", "proxy_unit": "USD"}, "session_date": str(latest), "session_type": "regular_session", "window_start": str(composite_start), "window_end": str(latest), "bar_interval": "5m", "source": {"provider": "Yahoo Finance via yfinance", "feed_scope": "US regular session", "delay_status": "unknown_or_delayed", "note": "供应商未确认实时权限；最新完整5分钟bar与报价时间分开记录"}, "index_quote": index_quote, "proxy_quote": proxy_quote, "proxy_mapping": _mapping(index_quote, proxy_quote, spec), "windows": {"today": today, "previous": prior, "composite_5": composite}, "current": {"proxy_price": proxy_quote.get("price"), "index_price": index_quote.get("price"), "relative_to_poc": ((proxy_quote.get("price") / today.get("poc")) - 1) if proxy_quote.get("price") and today.get("poc") else None, "relative_to_vwap": ((proxy_quote.get("price") / today.get("vwap")) - 1) if proxy_quote.get("price") and today.get("vwap") else None, "relative_to_value_area": "above_vah" if proxy_quote.get("price") and today.get("vah") and proxy_quote["price"] > today["vah"] else "below_val" if proxy_quote.get("price") and today.get("val") and proxy_quote["price"] < today["val"] else "inside_value_area"}, "options": _options(spec["options"], proxy_quote.get("as_of")), "judgment": _judgment(proxy_quote, today, prior), "quality": {"status": "observed", "volume_coverage": {"bar_count": today.get("included_bar_count", 0), "missing_volume_bar_count": today.get("missing_volume_bar_count", 0)}, "profile_estimation": "OHLCV分钟K线估算的成交量分布，不是逐笔真实成交价分布", "oi_limitation": "常规OI通常为前一结算日；本快照未将盘中成交量加到OI"}, "fetched_at": datetime.now(timezone.utc).isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(), "daily_model_as_of": None}
        card.update({
            "underlying": spec["proxy"],
            "price_unit": {"index": "index_points", "proxy": "USD", "options": "USD_strike"},
            "quote_as_of": {"index": index_quote.get("as_of"), "proxy": proxy_quote.get("as_of")},
            "profile_as_of": proxy_quote.get("as_of"),
            "options_quote_as_of": card["options"].get("options_quote_as_of"),
            "oi_as_of_date": card["options"].get("oi_as_of_date"),
            "delay_seconds": None,
            "coverage": card["quality"].get("volume_coverage"),
            "quality_status": card["quality"].get("status"),
            "missing_reason": None,
        })
        if index_quote.get("status") == "stale":
            card["quality_status"] = "partial_stale"
            card["missing_reason"] = "指数分钟报价本次刷新失败，沿用最近成功值"
            card["quality"]["status"] = "partial_stale"
            card["quality"]["missing_reason"] = card["missing_reason"]
        card["daily_model_as_of"] = None
        return card
    except Exception as exc:
        reason = f"抓取或计算失败：{exc}"
        if previous_card:
            return _stale_copy(previous_card, reason)
        return _unavailable(spec, reason)


def build_snapshot(refresh: bool = True) -> dict[str, Any]:
    run_id = f"index-intraday-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    cards = [build_card(spec, refresh=refresh) for spec in SPECS]
    fetched = datetime.now(timezone.utc).isoformat()
    return {"schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION, "run_id": run_id, "mode": "INDEX_INTRADAY_REFRESH", "generated_at": fetched, "fetched_at": fetched, "published_at": None, "display_timezone": "Asia/Shanghai", "cards": cards, "notes": ["指数点位没有自身可直接成交的逐笔价格量；POC/VA/VWAP来自明确标注的ETF交易结构代理。", "POC是观察窗口内成交最密集的价格区域，不等于仍持有筹码成本或必然回归磁铁。", "期权OI、成交量、IV和Greeks按字段分别记录；缺失不填0，不进入原方向概率。"]}


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def latest_dashboard(input_path: Path | None) -> tuple[dict[str, Any], Path | None]:
    if input_path and input_path.exists():
        return json.loads(input_path.read_text(encoding="utf-8")), input_path
    candidates = sorted((ROOT / "outputs").glob("dashboard-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("没有可合并的日线dashboard JSON，请先运行日更")
    return json.loads(candidates[0].read_text(encoding="utf-8")), candidates[0]


def refresh_dashboard(input_path: Path | None, output_path: Path, html_path: Path | None = None) -> dict[str, Any]:
    data, source_path = latest_dashboard(input_path)
    snapshot = build_snapshot(refresh=True)
    snapshot["daily_model_as_of"] = data.get("as_of")
    for card in snapshot["cards"]:
        card["daily_model_as_of"] = data.get("as_of")
    state_file = STATE_DIR / f"snapshot-{snapshot['run_id']}.json"
    atomic_write(state_file, snapshot)
    atomic_write(STATE_DIR / "latest.json", snapshot)
    data["index_intraday"] = snapshot
    data["index_intraday_run_id"] = snapshot["run_id"]
    data["index_intraday_generated_at"] = snapshot["generated_at"]
    data["index_intraday_published_at"] = None
    atomic_write(output_path, data)
    if html_path:
        from render_preview import render
        render(data, html_path)
    print(json.dumps({"mode": "INDEX_INTRADAY_REFRESH", "run_id": snapshot["run_id"], "source": str(source_path) if source_path else None, "output": str(output_path), "cards": [c["status"] for c in snapshot["cards"]]}, ensure_ascii=False))
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="抓取一次最新可得指数/代理分钟数据和期权链")
    parser.add_argument("--input", type=Path, help="日线dashboard JSON；省略时读取最新outputs/dashboard-*.json")
    parser.add_argument("--output", type=Path, default=ROOT / "site" / "data.json")
    parser.add_argument("--html", type=Path, default=ROOT / "site" / "index.html")
    args = parser.parse_args()
    if not args.refresh:
        raise SystemExit("请显式使用 --refresh；该命令每次只执行一次盘中刷新，不启动后台扫描。")
    refresh_dashboard(args.input, args.output, args.html)


if __name__ == "__main__":
    main()
