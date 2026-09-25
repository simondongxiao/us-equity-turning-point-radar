"""Desktop/mobile smoke checks against the generated live Pages artifact."""
from pathlib import Path
import os
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
            assert page.locator('#indexRows tr').count() == 9
            assert 'SOX' in page.locator('#indexSummary').inner_text()
            first = page.locator('#indexSummary').inner_text()
            page.locator('[data-horizon="5"]').click()
            assert page.locator('#indexSummary').inner_text().startswith('5交易日')
            page.locator('[data-horizon="10"]').click()
            assert page.locator('#indexSummary').inner_text() == first
            assert page.locator("#storageQuick").inner_text().startswith("存储与内存 · 4")
            assert page.locator("[aria-label='机会分排序：从大到小']").get_attribute("aria-pressed") == "true"
            page.locator("[aria-label='机会分排序：从大到小']").click()
            assert page.locator("[aria-label='机会分排序：从小到大']").get_attribute("aria-pressed") == "true"
            assert page.locator("#sortLabel").inner_text().startswith("机会分：从小到大")
            page.locator("[aria-label='机会分排序：从小到大']").click()
            page.locator("#storageQuick").click()
            assert page.locator("#stockRows tr").count() == 4
            page.select_option("#groupFilter", "存储与内存")
            page.select_option("#businessTagFilter", "NAND")
            assert set(page.locator("#stockRows button.symbol-button").all_text_contents()) == {"MU", "SNDK"}
            page.select_option("#businessTagFilter", "")
            stage = page.locator("#stageFilter option").nth(1).get_attribute("value")
            assert stage
            page.select_option("#stageFilter", stage)
            assert page.locator("#stockRows tr").count() > 0
            page.select_option("#stageFilter", "")
            page.locator("#storageSubFilter").select_option("nand-ssd")
            assert page.locator("#stockRows tr").count() == 2
            page.locator("[aria-label='阶段底概率排序：从大到小']").click()
            assert page.locator("#sortLabel").inner_text().startswith("阶段底概率：从大到小")
            page.locator("[aria-label='阶段顶概率排序：从大到小']").click()
            assert page.locator("#sortLabel").inner_text().startswith("阶段顶概率：从大到小")
            page.locator("[aria-label='参考价排序：从大到小']").click()
            assert page.locator("#sortLabel").inner_text().startswith("参考价：从大到小")
            page.locator("#clearFilters").click()
            assert page.locator("#storageQuick").get_attribute("aria-pressed") == "false"
            assert page.locator("#stockRows tr").count() == 100
            page.locator("#stockRows button.symbol-button", has_text="SNDK").click()
            detail = page.locator("#detailDialog")
            detail_text = detail.inner_text()
            assert '相对SOX与大盘的独立强弱' in detail_text
            for required in ("存储与内存", "剔除自身后的同行", "多周期潜在价带与概率", "候选顶底五步体系", "第一步·Price Structure", "第二步·Options Distribution", "第三步·候选位是否处于合理概率区间", "第四步·Options Skew / Put-Call", "第五步·Event / Catalyst", "最终候选底部区域", "最终候选顶部区域", "近一年免费数据滚动回测", "市场与板块是否同步", "基本面与事件证据", "尾部风险与执行"):
                assert required in detail_text
            page.locator("#closeDetail").click()
            page.locator("#tickerInput").fill("SNDK")
            page.locator("#runBtn").click()
            assert "任务接口尚未部署" in page.locator("#jobStatus").inner_text()
            page.close()
        browser.close()
    if errors:
        raise AssertionError("browser errors: " + "; ".join(errors))
    print("PASS: live desktop/mobile storage, filter, sorting and gateway-honesty checks")


if __name__ == "__main__":
    run()
