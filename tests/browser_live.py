"""Desktop/mobile smoke checks against the generated live Pages artifact."""
from pathlib import Path
import os
import re
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TARGET = os.environ.get("RADAR_LIVE_URL", (ROOT / "site" / "index.html").as_uri())


def run() -> None:
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for viewport in ((1440, 900), (390, 844)):
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(TARGET, wait_until="load")
            assert page.locator("#stockRows tr").count() == 100
            assert page.locator("#coverage").inner_text() == "100 / 100"
            as_of = page.evaluate("DATA.as_of")
            assert page.locator("#asOf").inner_text() == as_of
            assert "北京时间" in page.locator("#beijing").inner_text()
            weekly_text = page.locator("#weeklyPoolText").inner_text()
            assert "母池 5310" in weekly_text
            assert "完整人气分 1367" in weekly_text
            assert "自动调池：BLOCKED" in weekly_text
            assert page.locator('[aria-label="指数顶底概率"] h2').inner_text() == '指数方向性拐点'
            assert page.locator('#indexRows tr').count() == 13
            assert page.locator('#indexForecastRows tr').count() == 13
            index_names = set(page.locator('#indexForecastRows button').all_text_contents())
            for symbol in ('SOXL','SOXS','TQQQ','SQQQ'):
                assert any(name.startswith(symbol + ' ·') for name in index_names)
            assert 'SOX' in page.locator('#indexSummary').inner_text()
            first = page.locator('#indexSummary').inner_text()
            page.locator('[data-horizon="5"]').click()
            assert page.locator('#indexSummary').inner_text().startswith('5交易日')
            assert page.locator('[data-horizon="5"]').inner_text() == '5交易日（1周）'
            assert page.locator('#bottomHorizonHead').inner_text().endswith('5日')
            page.locator('[data-horizon="10"]').click()
            assert page.locator('#indexSummary').inner_text() == first
            assert page.locator('#bottomHorizonHead').inner_text().endswith('10日')
            page.locator('#calibrationPanel summary').click()
            assert page.locator('#calibrationBody .calibration-chart').count() == 1
            assert '多数类基线' in page.locator('#calibrationSummary').inner_text()
            assert page.locator('#calibrationBody .calibration-table tr').count() > 1
            page.locator('#indexTopSort').click()
            assert page.locator('#indexTopSort').get_attribute('aria-pressed') == 'true'
            page.locator('#indexTopSort').click()
            assert '↑' in page.locator('#indexTopSort').inner_text()
            page.locator('#indexForecastRows button').filter(has_text=re.compile(r'^SOX ·')).click()
            assert '独立时间检验' in page.locator('#detailBody').inner_text()
            assert '阶段底' in page.locator('#detailBody').inner_text()
            assert '路径重合' in page.locator('#detailBody').inner_text()
            page.locator('#closeDetail').click()
            page.locator('#indexForecastRows button').filter(has_text=re.compile(r'^SOXL ·')).click()
            leveraged_text = page.locator('#detailDialog').inner_text()
            assert 'ETF自身复权历史' in leveraged_text
            assert '不把基准指数概率乘以杠杆倍数或镜像' in leveraged_text
            assert '复利衰减' in leveraged_text
            page.locator('#closeDetail').click()
            assert page.locator("#storageQuick, #storageSubFilter, #clearFilters").count() == 0
            assert page.locator("[aria-label='机会分排序：从大到小']").get_attribute("aria-pressed") == "true"
            page.locator("[aria-label='机会分排序：从大到小']").click()
            assert page.locator("[aria-label='机会分排序：从小到大']").get_attribute("aria-pressed") == "true"
            assert page.locator("#sortLabel").inner_text().startswith("机会分：从小到大")
            page.locator("[aria-label='机会分排序：从小到大']").click()
            page.select_option("#bucketFilter", "科技与成长主题")
            page.select_option("#groupFilter", "存储与内存")
            assert page.locator("#stockRows tr").count() == 4
            assert page.locator("#storagePanel").is_visible()
            page.select_option("#businessTagFilter", "NAND")
            assert set(page.locator("#stockRows button.symbol-button").all_text_contents()) == {"MU", "SNDK"}
            page.select_option("#businessTagFilter", "")
            stage = page.locator("#stageFilter option").nth(1).get_attribute("value")
            assert stage
            page.select_option("#stageFilter", stage)
            assert page.locator("#stockRows tr").count() > 0
            page.select_option("#stageFilter", "")
            page.locator("[aria-label='方向性阶段底排序：从大到小']").click()
            assert page.locator("#sortLabel").inner_text().startswith("方向性阶段底：从大到小")
            page.locator("[aria-label='方向性阶段顶排序：从大到小']").click()
            assert page.locator("#sortLabel").inner_text().startswith("方向性阶段顶：从大到小")
            page.locator("[aria-label='参考价排序：从大到小']").click()
            assert page.locator("#sortLabel").inner_text().startswith("参考价：从大到小")
            page.select_option("#groupFilter", "")
            page.select_option("#bucketFilter", "")
            assert page.locator("#stockRows tr").count() == 100
            qcom_row = page.locator("#stockRows tr", has=page.locator("button.symbol-button", has_text="QCOM"))
            qcom_touch = page.evaluate("""() => {const m=DATA.records.find(r=>r.symbol==='QCOM').metrics['10'];return [m.p_upfirst,m.p_downfirst,m.p_unhit].map(v=>(v*100).toFixed(1)+'%')}""")
            assert f"上 {qcom_touch[0]}" in qcom_row.inner_text()
            page.locator("#stockRows button.symbol-button", has_text="QCOM").click()
            qcom_text = page.locator("#detailBody").inner_text()
            assert "多周期互斥方向拐点" in qcom_text
            assert " / ".join(qcom_touch) in qcom_text
            assert "双向洗盘是可重叠事件诊断" in qcom_text
            assert '数据与模型 · 信度' in qcom_text
            assert any(label in qcom_text for label in ('高确信','中确信','低可信/无明显偏向'))
            assert page.locator("#detailBody .fan-chart").count() == 1
            page.locator("#closeDetail").click()
            page.locator("#stockRows button.symbol-button", has_text="SNDK").click()
            detail = page.locator("#detailDialog")
            detail_text = detail.inner_text()
            assert '相对SOX与大盘的独立强弱' in detail_text
            for required in ("存储与内存", "剔除自身后的同行", "多周期互斥方向拐点", "候选顶底五步体系", "第一步·Price Structure", "第二步·Options Distribution", "第三步·合理区间核对", "第四步·Options Skew / Put-Call", "第五步·Event / Catalyst", "候选底部", "候选顶部", "近一年免费数据滚动回测", "市场与板块是否同步", "基本面与事件证据", "尾部风险与执行", "旧四态联合事件挑战者", "不作为方向性阶段顶/底决策概率"):
                assert required in detail_text, required
            assert detail.locator(".fan-chart").count() == 1
            stress = detail.locator("select[data-shadow-stress]")
            assert stress.count() == 1
            stress.select_option("-0.03")
            assert "-3.0%" in detail.locator("[data-shadow-stress-result]").inner_text()
            page.locator("#closeDetail").click()
            page.locator("#tickerInput").fill("SNDK")
            page.locator("#runBtn").click()
            assert "任务接口尚未部署" in page.locator("#jobStatus").inner_text()
            page.close()
        browser.close()
    if errors:
        raise AssertionError("browser errors: " + "; ".join(errors))
    print("PASS: live desktop/mobile simplified filters, storage, sorting and gateway-honesty checks")


if __name__ == "__main__":
    run()
