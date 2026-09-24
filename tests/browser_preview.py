"""Synthetic UI checks only. Does not validate market data, model accuracy or deployment."""
from pathlib import Path
import shutil,json
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'preview.html').read_text(encoding='utf-8')
checks=[]
with sync_playwright() as p:
    kwargs={'headless':True,'args':['--no-sandbox']}
    if shutil.which('chromium'):kwargs['executable_path']=shutil.which('chromium')
    browser=p.chromium.launch(**kwargs)
    page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(HTML,wait_until='load')
    assert page.locator('#stockRows tr').count()==100
    assert page.locator('#coverage').inner_text()=='100 / 0'
    checks.append('100 unchanged seeds and explicit 0 valid forecasts')
    page.screenshot(path=str(ROOT/'tests/preview-desktop.png'),full_page=False)
    for field,label in [('reference_price','参考价'),('risk_value','风险分'),('bottom_probability','阶段底概率'),('top_probability','阶段顶概率'),('opportunity_value','机会分')]:
        button=page.locator(f"[data-field='{field}']")
        button.click()
        assert f'{label}：从大到小' in page.locator('#sortLabel').inner_text()
        button.click()
        assert f'{label}：从小到大' in page.locator('#sortLabel').inner_text()
    checks.append('single-button direction toggle for reference price, opportunity/risk scores and independent bottom/top probabilities')
    page.locator('[data-horizon="5"]').click()
    assert '5个交易日' in page.locator('#horizonLabel').inner_text()
    page.select_option('#bucketFilter','科技与成长主题');assert page.locator('#stockRows tr').count()==60
    page.select_option('#bucketFilter','非科技主题');assert page.locator('#stockRows tr').count()==40
    checks.append('60 tech / 40 non-tech theme filters')
    page.locator('#storageQuick').click()
    assert set(page.locator('#stockRows .symbol-button').all_text_contents())=={'MU','SNDK','WDC','STX'}
    assert page.locator('#storagePanel').is_visible()
    assert not page.locator('#storageSubFilter').is_disabled()
    assert '尚无同一数据时点' in page.locator('#storageSummaryText').inner_text()
    assert page.locator('#coverage').inner_text()=='100 / 0'
    checks.append('storage shortcut shows the four original names, no invented rotation')
    for value,expected in [('dram-hbm',{'MU'}),('nand-ssd',{'MU','SNDK'}),('hdd',{'WDC','STX'})]:
        page.select_option('#storageSubFilter',value)
        assert set(page.locator('#stockRows .symbol-button').all_text_contents())==expected
    checks.append('DRAM/HBM, NAND/SSD, HDD filters; overlapping MU not duplicated')
    page.select_option('#storageSubFilter','')
    page.select_option('#businessTagFilter','NAND')
    assert set(page.locator('#stockRows .symbol-button').all_text_contents())=={'MU','SNDK'}
    page.select_option('#businessTagFilter','')
    stage=page.locator('#stageFilter option').nth(1).get_attribute('value')
    assert stage
    page.select_option('#stageFilter',stage)
    assert page.locator('#stockRows tr').count() > 0
    checks.append('research-group, business-tag and stage dropdowns filter without changing scores')
    page.select_option('#bucketFilter','非科技主题')
    assert page.locator('#stockRows tr').count()==40
    assert page.locator('#storageSubFilter').is_disabled()
    assert page.locator('#storageSubFilter').input_value()==''
    checks.append('incompatible theme changes clear storage subfilter')
    page.locator('#clearFilters').click();assert page.locator('#stockRows tr').count()==100
    assert '机会分：从小到大' in page.locator('#sortLabel').inner_text()
    assert '5个交易日' in page.locator('#horizonLabel').inner_text()
    checks.append('clear filter preserves sort and horizon')
    page.locator('#storageQuick').click();page.locator('[data-horizon="10"]').click()
    page.locator("[data-field='opportunity_value']").click()
    page.screenshot(path=str(ROOT/'tests/preview-storage-desktop.png'),full_page=False)
    page.locator('#stockRows .symbol-button').filter(has_text='SNDK').click()
    detail=page.locator('#detailBody').inner_text()
    for title in ['研究分组与业务标签','NAND / SSD','剔除自身后的同行','多周期潜在价带与概率','候选顶底五步体系','第一步·Price Structure','第二步·Options Distribution','第三步·候选位是否处于合理概率区间','第四步·Options Skew / Put-Call','第五步·Event / Catalyst','最终候选底部区域','最终候选顶部区域','市场与板块是否同步','确认与失效条件','基本面与事件证据','尾部风险与执行','首次触达路径','数据与模型']:assert title in detail,title
    page.screenshot(path=str(ROOT/'tests/preview-sndk-detail.png'),full_page=False)
    page.locator('#closeDetail').click()
    checks.append('SNDK detail retains all original research sections plus taxonomy and LOO')
    page.locator('#tickerInput').fill('MU');page.locator('#runBtn').click()
    assert page.locator('#detailDialog').evaluate('(e)=>e.open')
    assert '尚未部署' in page.locator('#jobStatus').inner_text()
    page.locator('#closeDetail').click()
    page.locator('#tickerInput').fill('TEST-NOT-A-STOCK');page.locator('#runBtn').click()
    assert '不能运行新的个股分析' in page.locator('#jobStatus').inner_text()
    assert page.locator('#temporaryTab').inner_text().endswith('0')
    checks.append('unchanged missing-gateway honesty; no fake pool-external result')
    page.locator('#edgeOnly').check();assert page.locator('#stockRows tr').count()==0
    assert page.locator('#emptyState').is_visible();page.locator('#edgeOnly').uncheck()
    checks.append('positive-edge filter does not promote uncalibrated seeds')
    # Ephemeral synthetic data: discarded before preview screenshots and never written to JSON.
    page.evaluate("""() => {
      const fixture={MU:[.1,.3,21,.2,.4],SNDK:[.3,.1,87,.7,.1],WDC:[-.1,.2,4,.3,.5],STX:[null,null,null,null,null]};
      for(const r of regular){if(fixture[r.symbol]){const [o,k,score,bottom,top]=fixture[r.symbol];r.metrics['10']={status:o===null?'uncalibrated':'calibrated',opportunity_value:o,risk_value:k,p_bottom:bottom,p_top:top,opportunity_score:score,risk_score:k,expected_return:o,es95:k,positive_edge:o>0};}}
      render();
    }""")
    for field,expected in [('opportunity_value',['WDC','MU','SNDK','STX']),('opportunity_value',['SNDK','MU','WDC','STX']),('risk_value',['MU','WDC','SNDK','STX']),('risk_value',['SNDK','WDC','MU','STX']),('bottom_probability',['SNDK','WDC','MU','STX']),('bottom_probability',['MU','WDC','SNDK','STX']),('top_probability',['WDC','MU','SNDK','STX']),('top_probability',['SNDK','MU','WDC','STX'])]:
        page.locator(f"[data-field='{field}']").click()
        assert page.locator('#stockRows .symbol-button').all_text_contents()==expected
    page.select_option('#storageSubFilter','nand-ssd')
    assert page.locator('#stockRows tr').count()==2
    assert page.evaluate("regular.find(r=>r.symbol==='SNDK').metrics['10'].opportunity_score")==87
    checks.append('numeric four-direction sorting in storage, null always last, scores unchanged by filter')
    # A synthetic new temporary member only, no backend or authorization claim.
    page.evaluate("""() => {temporary.push({symbol:'TEST-STORAGE',name_zh:'合成测试',coverage_bucket:'科技与成长主题',research_group:'存储与内存',research_group_id:'storage-memory',business_tags:['NAND','SSD'],metrics:{}});state.view='temporary';render();}""")
    assert page.locator('#stockRows .symbol-button').all_text_contents()==['TEST-STORAGE']
    assert page.locator('#coverage').inner_text().startswith('100 /')
    checks.append('temporary storage member dynamically filterable without changing 100-stock pool')
    # New browser document avoids re-declaring top-level JS constants in set_content.
    page.close()
    page=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(HTML,wait_until='load')
    page.locator('#storageQuick').click()
    page.select_option('#storageSubFilter','hdd')
    assert page.locator('#stockRows tr').count()==2
    page.select_option('#storageSubFilter','')
    page.screenshot(path=str(ROOT/'tests/preview-mobile.png'),full_page=False)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
    assert not errors,errors
    checks.append('mobile storage/subgroup controls, no whole-page horizontal overflow, no JS errors')
    browser.close()
report={'test_type':'synthetic_ui_only','checks':checks,'check_groups':len(checks),'result':'PASS','financial_backtest':False,'github_live_test':False,'gateway_test':False}
(ROOT/'tests/ui-check-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(f'PASS: {len(checks)} browser check groups; synthetic UI only, no financial/deployment validation.')
