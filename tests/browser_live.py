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
            assert page.locator("#storageQuick").inner_text().startswith("存储与内存 · 4")
            assert page.locator("[aria-label='机会从大到小']").get_attribute("aria-pressed") == "true"
            page.locator("#storageQuick").click()
            assert page.locator("#stockRows tr").count() == 4
            page.locator("#storageSubFilter").select_option("nand-ssd")
            assert page.locator("#stockRows tr").count() == 2
            page.locator("[aria-label='机会从小到大']").click()
            assert page.locator("#sortLabel").inner_text().startswith("机会：从小到大")
            page.locator("#clearFilters").click()
            assert page.locator("#storageQuick").get_attribute("aria-pressed") == "false"
            assert page.locator("#stockRows tr").count() == 100
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
