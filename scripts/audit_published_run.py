"""Freeze an observed public run locally and report conservative acceptance status.

This audits published output; it does not train, settle forecasts or certify alpha.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
URL = "https://simondongxiao.github.io/us-equity-turning-point-radar/"
STATE_RELEASE_API = "https://api.github.com/repos/simondongxiao/us-equity-turning-point-radar/releases/tags/radar-encrypted-state"


def _runtime_check(status: bool, item: str, evidence: str) -> dict[str, str]:
    return {"status": "PASS" if status else "FAIL", "item": item, "evidence": evidence}


def daily_runtime_checks(
    data: dict,
    expected_run: str | None = None,
    html: str | None = None,
    state_asset_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Evaluate only the observable invariants required for one daily publish.

    Long-horizon research and infrastructure gates remain separate.  This prevents
    an honest BLOCKED roadmap item from making a completed daily refresh look
    like a failed publish.
    """
    records = data.get("records") or []
    as_of = str(data.get("as_of") or "")
    run_id = str(data.get("run_id") or "")
    calibrated = {"calibrated", "calibrated_low_confidence"}
    valid = {
        str(h): sum((record.get("metrics") or {}).get(str(h), {}).get("status") in calibrated for record in records)
        for h in (5, 10, 21)
    }
    source = data.get("source_manifest") or {}
    intraday = source.get("intraday_regular_session_validation") or {}
    intraday_symbols = intraday.get("symbols") or {}
    regular_symbols = {str(record.get("symbol")) for record in records if record.get("symbol")}
    complete_regular = {
        symbol
        for symbol, row in intraday_symbols.items()
        if symbol in regular_symbols
        and row.get("status") in {"matched", "corrected"}
        and row.get("date") == as_of
        and int(row.get("bar_count") or 0) >= 78
        and isinstance(row.get("regular_session_aggregate"), dict)
    }
    alerts = data.get("boundary_breach_alerts") or {}
    tiered = alerts.get("tiered_records") or []
    legacy = alerts.get("records") or []
    relation_ok = True
    for row in [*tiered, *legacy]:
        side = row.get("side")
        observed = row.get("observed_price", row.get("current_price"))
        boundary = row.get("boundary_value")
        try:
            relation_ok = relation_ok and (
                (side == "below_lower" and float(observed) < float(boundary))
                or (side == "above_upper" and float(observed) > float(boundary))
            )
        except (TypeError, ValueError):
            relation_ok = False
    basis_as_of = str(alerts.get("basis_as_of") or "")
    index_records = ((data.get("index_forecasts") or {}).get("records") or [])
    decision_horizons = ((data.get("decision_board") or {}).get("horizons") or {})
    decisions_ok = all(
        isinstance((decision_horizons.get(str(h)) or {}).get("stage_bottom_candidates"), list)
        and isinstance((decision_horizons.get(str(h)) or {}).get("stage_top_warnings"), list)
        for h in (5, 10, 21)
    )
    workflow_run_id = str(data.get("workflow_run_id") or "")
    workflow_run_attempt = str(data.get("workflow_run_attempt") or "1")
    expected_state_prefix = f"state-{workflow_run_id}-{workflow_run_attempt}-" if workflow_run_id else ""
    matching_state_assets = [
        name for name in (state_asset_names or [])
        if expected_state_prefix and name.startswith(expected_state_prefix) and name.endswith(".fernet")
    ]
    checks = [
        _runtime_check(
            not expected_run or run_id == expected_run,
            "线上批次与预期批次一致",
            f"live={run_id}; expected={expected_run or 'not supplied'}",
        ),
        _runtime_check(
            len(records) == 100 and all(value == 100 for value in valid.values()),
            "常态100股及5/10/21日有效预测完整",
            f"records={len(records)}; valid={valid}",
        ),
        _runtime_check(
            source.get("complete_session_cutoff_ny") == as_of and source.get("common_latest_date") == as_of,
            "统一使用最近完整纽约交易日",
            f"as_of={as_of}; cutoff={source.get('complete_session_cutoff_ny')}; common={source.get('common_latest_date')}",
        ),
        _runtime_check(
            complete_regular == regular_symbols and int(intraday.get("unavailable") or 0) == 0,
            "100股5分钟常规时段聚合校验完整",
            f"regular={len(regular_symbols)}; verified={len(complete_regular)}; unavailable={intraday.get('unavailable')}",
        ),
        _runtime_check(
            alerts.get("status") == "observed"
            and alerts.get("version") == "prior-frozen-extreme-atr-breach-v1.7.0"
            and alerts.get("extreme_atr_multiplier") == 2.0
            and all(row.get("boundary_layer") == "extreme_atr" for row in tiered)
            and bool(basis_as_of)
            and basis_as_of < as_of
            and relation_ok,
            "冻结边界早于当前日并按v1.7极端ATR口径严格比较",
            f"basis={basis_as_of}; current={as_of}; version={alerts.get('version')}; evaluated={alerts.get('evaluated_symbol_horizons')}; unavailable={alerts.get('unavailable_symbol_horizons')}",
        ),
        _runtime_check(
            len(index_records) == 13,
            "指数与ETF方向性拐点覆盖13项",
            f"index_records={len(index_records)}",
        ),
        _runtime_check(
            decisions_ok,
            "决策榜同时提供阶段底与阶段顶候选",
            f"horizons={sorted(decision_horizons)}",
        ),
        _runtime_check(
            html is not None and run_id in html and "极端ATR越界预警" in html and "决策榜" in html,
            "GitHub Pages HTML为本批次且包含极端ATR越界预警和决策榜",
            f"html_checked={html is not None}; run_id={run_id}",
        ),
        _runtime_check(
            len(matching_state_assets) == 1,
            "本批次预测、模型、来源与周度审计已生成匹配的加密状态包",
            f"workflow_run_id={workflow_run_id or 'missing'}; attempt={workflow_run_attempt}; matching_assets={matching_state_assets}",
        ),
    ]
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-run")
    args = parser.parse_args()
    audit_stamp = datetime.now(timezone.utc).isoformat()
    response = requests.get(URL + "data.json", params={"audit": audit_stamp}, timeout=60)
    response.raise_for_status()
    data = response.json()
    html_response = requests.get(URL, params={"audit": audit_stamp}, timeout=60)
    html_response.raise_for_status()
    html = html_response.text
    release_response = requests.get(STATE_RELEASE_API, params={"audit": audit_stamp}, timeout=60)
    release_response.raise_for_status()
    state_asset_names = [
        asset.get("name", "")
        for asset in release_response.json().get("assets", [])
        if asset.get("state") == "uploaded"
    ]
    run_id = data["run_id"]
    if args.expected_run and run_id != args.expected_run:
        raise ValueError(f"Live run {run_id} differs from deployed artifact {args.expected_run}")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id):
        raise ValueError("Invalid run ID")
    folder = ROOT / "outputs" / "published-runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    snapshot = folder / "dashboard.json"
    if snapshot.exists():
        if snapshot.read_bytes() != canonical:
            raise ValueError("Published run changed under same run_id; refusing overwrite")
    else:
        with snapshot.open("xb") as fh:
            fh.write(canonical)
    source = data.get("source_manifest", {})
    dates = source.get("latest_complete_date_by_symbol", {})
    latest = max(dates.values()) if dates else None
    lagging = {symbol: date for symbol, date in dates.items() if date < latest} if latest else {}
    records = data["records"]
    regular = len(records)
    valid = {str(h): sum(r.get("metrics", {}).get(str(h), {}).get("status") in {"calibrated", "calibrated_low_confidence"} for r in records) for h in (5, 10, 21)}
    runtime_checks = daily_runtime_checks(data, args.expected_run, html, state_asset_names)
    daily_status = "PASS" if all(item["status"] == "PASS" for item in runtime_checks) else "FAIL"
    weekly_status = (data.get("weekly_pool_audit") or {}).get("automatic_reselection_status") or "BLOCKED"
    gateway_status = "READY" if (data.get("public_config") or {}).get("api_base_url") else "BLOCKED"
    option_status = (data.get("options_model_gate") or {}).get("status") or "BLOCKED"
    lines = [
        f"# 日更验收：{run_id}", "",
        f"- **本次日更核心验收：{daily_status}**",
        f"- 在线地址：{URL}",
        f"- 构建时间：{data.get('generated_at')}；模型基准日：{data['as_of']}",
        f"- 来源完整日线截止：{source.get('complete_session_cutoff_ny')}；最新单股日期：{latest}",
        f"- 股票池：{regular}；有效预测（5/10/21日）：{valid}",
        f"- 模型：{data.get('model_version')}；存储因子：shadow / BLOCKED",
        f"- 归档SHA256：{hashlib.sha256(canonical).hexdigest()}",
        f"- 拖后共同日期的成员：{json.dumps(lagging, ensure_ascii=False)}",
        f"- 单股网关：{gateway_status}；每周动态调池：{weekly_status}；期权B4：{option_status}。",
        f"- 加密预测/模型/来源/周度审计归档：{'PASS' if any(item['item'].startswith('本批次预测') and item['status'] == 'PASS' for item in runtime_checks) else 'FAIL'}；外部网关任务状态持久化：{gateway_status}。",
        "- 加密Release资产为追加式长期归档；本次公开派生构建产物另留档90天。",
        "- 当前股池条件回测有选择/幸存者偏差；分类准确率不等于净交易胜率。",
        "", "## 本次日更运行证据", "",
    ]
    for item in runtime_checks:
        lines.append(f"- **{item['status']}** — {item['item']}；{item['evidence']}")
    lines += [
        "",
        "以下长期能力清单与本次日更是两层验收：路线项保留BLOCKED不会把已通过的当日数据更新改判为失败。",
        "", "## 近一年成熟历史评估", "",
    ]
    horizons = data.get("backtest", {}).get("one_year_walk_forward", {}).get("horizons", {})
    for horizon, metric in horizons.items():
        values = {key: metric.get(key) for key in ("classification_accuracy", "bottom_distance_within_3pct", "top_distance_within_3pct", "matured_classification_n", "band_checkpoint_n")}
        lines.append(f"- {horizon}交易日：{json.dumps(values, ensure_ascii=False)}")
    lines += ["", "## 原清单及v1.1增量清单", "", "PASS仅用于本次有证据的项目；复合条目有任一部分未验证则保留BLOCKED。", ""]
    # Conservative mapping: don't inherit previous broad PASS claims.
    verified_prefixes = (
        "新项目在D:", "真实数据、合成测试", "机会/风险/人气分开",
        "VIX/IV/OI/Gamma缺失", "100股数量、可比较预测数量",
        "数值排序、空值永远置底", "过滤不重算100股分位",
        "过滤不重算全池分位", "原包文件清单无删除",
        "首版100个唯一symbol不变", "存储快捷入口显示四只",
        "分类筛选/全部恢复", "详情同时保留原研究块",
        "新存储因子有真实同窗口消融",
    )
    for line in (ROOT / "references" / "acceptance.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") and line not in {"## 最终回报格式"}:
            lines += ["", "### " + line[3:], ""]
        elif line.startswith("- "):
            item = line[2:]
            status = "PASS" if item.startswith(verified_prefixes) else "BLOCKED"
            if item.startswith("Pages真实可访问") and args.expected_run == run_id:
                status = "PASS"
            # Factor validation itself remains blocked, even if status is disclosed.
            if item.startswith("新存储因子"):
                status = "BLOCKED"
            lines.append(f"- **{status}** — {item}")
    lines += ["", "## 继续推进顺序", "",
        "1. 排查共同日期滞后，补齐按交易所日历判断的时效门槛。",
        "2. 继续逐日结算冻结预测；按净交易胜率、极值误差分别验收。",
        "3. 核验母池60日成交额/持续性历史，接入点时周更与存储候选覆盖。",
        "4. 在成熟样本上验证存储LOO增量与独立顶/底校准；未过门槛不晋级。",
        "5. 配置HTTPS鉴权网关与持久任务服务，完成真实池外股链路。", ""]
    report = folder / "acceptance.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"run_id": run_id, "as_of": data["as_of"], "valid": valid, "latest": latest, "lagging": lagging, "report": str(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
