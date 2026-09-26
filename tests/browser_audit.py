"""Validate the audit controls on desktop and mobile, including JS errors."""
from pathlib import Path
import os
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
target=os.environ.get('RADAR_AUDIT_URL',(ROOT/'outputs/validation-preview.html').as_uri())
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    for width in (1440,390):
        page=browser.new_page(viewport={'width':width,'height':900});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(target)
        for h in ('5','10','21'):
            page.select_option('#h',h)
            for model in ('technical','market_residual'):
                page.select_option('#model',model)
                assert page.locator('#metrics tr').count()==3
                assert page.locator('#folds tr').count()==5
                assert page.locator('svg').count()==2
        assert not errors,errors
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.close()
    browser.close()
print('PASS audit desktop/mobile: 12 horizon/model combinations; no JS errors')
