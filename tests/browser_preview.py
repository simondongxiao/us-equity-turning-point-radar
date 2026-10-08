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
    assert page.locator('[aria-label="指数顶底概率"] h2').inner_text()=='指数方向性拐点'
    assert page.locator('#stockRows tr').count()==100
    assert page.locator('#coverage').inner_text()=='100 / 0'
    assert '当前100股保持不变' in page.locator('#weeklyPoolText').inner_text()
    page.evaluate("""() => {DATA.weekly_pool_audit={as_of:'2026-09-28',mother_pool_observed_count:5310,complete_popularity_security_count:1367,regular_pool_count:100,current_member_source_gaps:[{symbol:'BRK-B'},{symbol:'GOOGL'}],storage_candidate_ranks:{WDC:32,MU:72,SNDK:78,STX:80,NTAP:236},automatic_reselection_status:'BLOCKED'};renderWeeklyPool();}""")
    assert '母池 5310' in page.locator('#weeklyPoolText').inner_text()
    assert '完整人气分 1367' in page.locator('#weeklyPoolText').inner_text()
    assert 'BRK-B / GOOGL' in page.locator('#weeklyPoolText').inner_text()
    assert '自动调池：BLOCKED' in page.locator('#weeklyPoolText').inner_text()
    page.evaluate("""() => {DATA.boundary_breach_alerts={status:'observed',current_as_of:'2026-10-07',basis_as_of:'2026-10-06',basis_run_id:'prior-run',evaluated_symbol_horizons:339,unavailable_symbol_horizons:0,note:'越界不是交易信号。',records:[{symbol:'SNDK',scope:'stock',horizon_sessions:5,side:'below_lower',current_price:89,boundary_value:90,breach_pct:-.0111},{symbol:'SOX',scope:'index_etf',horizon_sessions:10,side:'above_upper',current_price:111,boundary_value:110,breach_pct:.0091}]};renderBoundaryAlerts();}""")
    assert page.locator('#boundaryAlertPanel').bounding_box()['y'] < page.locator('[aria-label="数据状态"]').bounding_box()['y']
    assert page.locator('#boundaryAlertRows tr').count()==3
    assert 'SNDK' in page.locator('#boundaryAlertRows').inner_text()
    assert 'SOX' in page.locator('#boundaryAlertRows').inner_text()
    assert '2026-10-06' in page.locator('#boundaryAlertMeta').inner_text()
    assert '越界 2 项' in page.locator('#boundaryAlertCount').inner_text()
    checks.append('top-of-home prior-frozen 5/10/21-day lower/upper boundary breach alerts')
    page.evaluate("""() => {DATA.decision_board={horizons:{'5':{stage_bottom_candidates:[{side:'stage-bottom',decision_rank:1,symbol:'MU',stage_primary_judgment:'bottom_reversal_candidate',directional_probability:.42,zone:[90,95],distance_to_zone_pct:.03,joint_probability:.31,confidence_score:74}],stage_top_warnings:[{side:'stage-top',decision_rank:1,symbol:'SNDK',stage_primary_judgment:'top_reversal_warning',directional_probability:.47,zone:[105,110],distance_to_zone_pct:.05,joint_probability:.36,confidence_score:71}]},'10':{stage_bottom_candidates:[{side:'stage-bottom',decision_rank:1,symbol:'MU',stage_primary_judgment:'bottom_reversal_candidate',directional_probability:.42,zone:[90,95],distance_to_zone_pct:.03,joint_probability:.31,confidence_score:74}],stage_top_warnings:[{side:'stage-top',decision_rank:1,symbol:'SNDK',stage_primary_judgment:'top_reversal_warning',directional_probability:.47,zone:[105,110],distance_to_zone_pct:.05,joint_probability:.36,confidence_score:71}]},'21':{stage_bottom_candidates:[],stage_top_warnings:[]}}};renderDecisionBoard();}""")
    assert page.locator('#decisionPanel').count()==1
    assert page.locator('[data-decision-side]').count()==2
    assert page.locator('[data-decision-side="bottom"]').get_attribute('aria-pressed')=='true'
    assert page.locator('#decisionRows tr').count()==1
    assert '阶段底候选' in page.locator('#decisionRows').inner_text()
    page.locator('[data-decision-side="top"]').click()
    assert page.locator('[data-decision-side="top"]').get_attribute('aria-pressed')=='true'
    assert page.locator('#decisionRows tr').count()==1
    assert '阶段顶部候选' in page.locator('#decisionRows').inner_text()
    page.locator('[data-decision-side="bottom"]').click()
    assert '阶段底候选' in page.locator('#decisionRows').inner_text()
    assert page.locator('#decisionPanel').bounding_box()['y'] > page.locator('.stock-table').bounding_box()['y']
    checks.append('100 unchanged seeds, explicit 0 valid forecasts and visible weekly-pool audit status')
    page.evaluate("""() => {DATA.as_of='2026-10-07';DATA.index_context={as_of:'2026-10-07',rows:[{symbol:'SPY',name:'SPY · 标普500 ETF',kind:'etf_proxy',as_of:'2026-10-07',level:100,returns:{'1':.01,'10':.02}}]};DATA.index_forecasts={as_of:'2026-10-07',records:[{symbol:'SPY',name:'SPY · 标普500 ETF',kind:'etf_proxy',as_of:'2026-10-07',level:100,metrics:{'10':{status:'calibrated',p_bottom_rebound_first:.55,p_top_reversal_first:.2,p_no_directional_turn:.25,p_two_way_wash:.1,bottom_band:[92,95],top_band:[106,109],confidence_level:'medium',confidence_label:'中确信'}}}],structure_matrix:{as_of:'2026-10-07',note:'结构价位与压力区不是概率。',records:[{symbol:'SPY',name:'SPY · 标普500 ETF',status:'observed',horizon_sessions:10,horizon_label:'2周',horizon_end_date:'2026-10-21',anchor_price:100,anchor_as_of:'2026-10-07',core_bottom_zone:[92,95],core_top_zone:[106,109],core_bottom_source:'turning_point_conditional_path',core_top_source:'turning_point_conditional_path',core_bottom_relative_to_anchor_pct:[-.08,-.05],core_top_relative_to_anchor_pct:[.06,.09],pressure_bottom_zone:[88,92],pressure_top_zone:[109,115],pressure_bottom_relative_to_anchor_pct:[-.12,-.08],pressure_top_relative_to_anchor_pct:[.09,.15],price_location_label:'位于核心底顶区之间',main_judgment:'方向模型偏阶段底 55.0%；中确信。',trigger_condition:'触及底区后确认',invalidation_condition:'下破88或上破115后重算',event_status:'not_ingested',wide_range_flag:true}]}};renderIndices();}""")
    assert page.locator('#indexStructureRows tr').count()==1
    matrix_text=page.locator('#indexStructureRows').text_content()
    assert '2026-10-21' in matrix_text,matrix_text
    assert '不是概率' in page.locator('#indexStructureNote').text_content()
    assert 'not_ingested' in page.locator('#indexStructureRows').text_content()
    checks.append('index structure matrix keeps price zones, anchor distances, event state and directional probabilities semantically separate')
    page.screenshot(path=str(ROOT/'tests/preview-desktop.png'),full_page=False)
    for field,label in [('reference_price','参考价'),('risk_value','尾部风险排序'),('bottom_probability','方向性阶段底'),('top_probability','方向性阶段顶'),('opportunity_value','收益机会排序')]:
        button=page.locator(f"[data-field='{field}']")
        button.click()
        assert f'{label}：从大到小' in page.locator('#sortLabel').inner_text()
        button.click()
        assert f'{label}：从小到大' in page.locator('#sortLabel').inner_text()
    checks.append('single-button direction toggle for reference price, opportunity/risk scores and exclusive directional bottom/top probabilities')
    page.locator('[data-horizon="5"]').click()
    assert '5个交易日' in page.locator('#horizonLabel').inner_text()
    assert page.locator('[data-horizon="5"]').inner_text()=='5交易日（1周）'
    assert page.locator('#bottomHorizonHead').inner_text().endswith('5日')
    page.locator('#calibrationPanel summary').click()
    assert '尚无成熟样本' in page.locator('#calibrationSummary').inner_text()
    assert '不能补画理想曲线' in page.locator('#calibrationBody').inner_text()
    page.select_option('#bucketFilter','科技与成长主题');assert page.locator('#stockRows tr').count()==60
    page.select_option('#bucketFilter','非科技主题');assert page.locator('#stockRows tr').count()==40
    checks.append('60 tech / 40 non-tech theme filters')
    for removed in ('#storageQuick','#storageSubFilter','#clearFilters'):
        assert page.locator(removed).count()==0
    assert page.locator('#businessTagFilter').count()==0
    assert page.locator('.filter-row #bucketFilter').count()==1
    page.select_option('#bucketFilter','科技与成长主题')
    page.select_option('#groupFilter','存储与内存')
    assert set(page.locator('#stockRows .symbol-button').all_text_contents())=={'MU','SNDK','WDC','STX'}
    assert page.locator('#storagePanel').is_visible()
    assert '尚无同一数据时点' in page.locator('#storageSummaryText').inner_text()
    assert page.locator('#coverage').inner_text()=='100 / 0'
    checks.append('removed duplicate storage/subgroup/all-stock controls; research-group dropdown keeps the four original names')
    stage=page.locator('#stageFilter option').nth(1).get_attribute('value')
    assert stage
    page.select_option('#stageFilter',stage)
    assert page.locator('#stockRows tr').count() > 0
    checks.append('research-group and stage dropdowns filter without changing scores; business-tag menu removed')
    page.select_option('#stageFilter','')
    page.select_option('#bucketFilter','非科技主题')
    assert page.locator('#stockRows tr').count()==40
    assert page.locator('#storagePanel').is_hidden()
    checks.append('incompatible theme changes clear the storage research-group selection')
    page.select_option('#bucketFilter','');assert page.locator('#stockRows tr').count()==100
    assert '收益机会排序：从小到大' in page.locator('#sortLabel').inner_text()
    assert '5个交易日' in page.locator('#horizonLabel').inner_text()
    checks.append('returning dropdowns to all preserves sort and horizon')
    page.select_option('#bucketFilter','科技与成长主题');page.select_option('#groupFilter','存储与内存');page.locator('[data-horizon="10"]').click()
    page.locator("[data-field='opportunity_value']").click()
    page.screenshot(path=str(ROOT/'tests/preview-storage-desktop.png'),full_page=False)
    page.locator('#stockRows .symbol-button').filter(has_text='SNDK').click()
    detail=page.locator('#detailBody').inner_text()
    for title in ['研究分组与业务标签','NAND / SSD','剔除自身后的同行','多周期互斥方向拐点','候选顶底五步体系','第一步·Price Structure','第二步·Options Distribution','第三步·合理区间核对','第四步·Options Skew / Put-Call','第五步·Event / Catalyst','候选底部','候选顶部','市场与板块是否同步','确认与失效条件','基本面与事件证据','尾部风险与执行','首次触达路径','数据与模型 · 信度']:assert title in detail,title
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
      for(const r of regular){if(fixture[r.symbol]){const [o,k,score,bottom,top]=fixture[r.symbol];r.metrics['10']={status:o===null?'uncalibrated':'calibrated',opportunity_value:o,risk_value:k,p_bottom:bottom,p_top:top,p_bottom_rebound_first:bottom,p_top_reversal_first:top,p_no_directional_turn:bottom===null?null:1-bottom-top,p_two_way_wash:.2,two_way_wash_flag:'normal',p_upfirst:.65,p_downfirst:.25,p_unhit:.10,volatility_bottom_band:[90,95],volatility_top_band:[105,110],opportunity_score:score,risk_score:k,expected_return:o,es95:k,positive_edge:o>0,confidence_level:'medium',confidence_label:'中确信',confidence_score:64,path_similarity_score:61,validation_ece:.09,validation_accuracy:.58,validation_majority_baseline_accuracy:.54,validation_accuracy_lift:.04,validation_test_n:120,validation_unique_dates:60,regime_shift_flag:false,confidence_reason:'合成界面测试'};}}
      DATA.backtest.one_year_directional_turn.horizons['10']={status:'observed',matured_classification_n:120,unique_evaluation_dates:60,classification_accuracy:.58,majority_baseline_accuracy:.54,accuracy_lift_vs_majority_baseline:.04,calibration_ece:.09,calibration_curve:{bottom_rebound_first:[{predicted_mean:.25,observed_rate:.22,n:40,date_block_ci_low:.15,date_block_ci_high:.30}],top_reversal_first:[{predicted_mean:.35,observed_rate:.38,n:40,date_block_ci_low:.29,date_block_ci_high:.47}],no_directional_turn:[{predicted_mean:.40,observed_rate:.40,n:40,date_block_ci_low:.31,date_block_ci_high:.49}]}};
      DATA.audit_upgrade={latest:{'10':[{symbol:'SNDK',reference:100,p_bottom:.6,p_top:.7,bottom_band_ratio:[.88,.91,.94],top_band_ratio:[1.06,1.09,1.12],fan:{sessions:[0,1,2],p25:[1,.98,.96],p50:[1,1.01,1.03],p75:[1,1.04,1.08]},attribution:[{group:'市场/指数',bottom_delta:.03,top_delta:-.02}],stress:[{market_shock:-.015,p_bottom:.65,p_top:.63},{market_shock:0,p_bottom:.6,p_top:.7}]}]}};
      render();
    }""")
    for field,expected in [('opportunity_value',['WDC','MU','SNDK','STX']),('opportunity_value',['SNDK','MU','WDC','STX']),('risk_value',['MU','WDC','SNDK','STX']),('risk_value',['SNDK','WDC','MU','STX']),('bottom_probability',['SNDK','WDC','MU','STX']),('bottom_probability',['MU','WDC','SNDK','STX']),('top_probability',['WDC','MU','SNDK','STX']),('top_probability',['SNDK','MU','WDC','STX'])]:
        page.locator(f"[data-field='{field}']").click()
        assert page.locator('#stockRows .symbol-button').all_text_contents()==expected
    page.select_option('#stageFilter','')
    page.select_option('#bucketFilter','科技与成长主题')
    page.select_option('#groupFilter','存储与内存')
    assert page.locator('#stockRows tr').count()==4
    assert '上 65.0%' in page.locator('#stockRows tr',has=page.locator('button',has_text='SNDK')).inner_text()
    assert page.evaluate("regular.find(r=>r.symbol==='SNDK').metrics['10'].opportunity_score")==87
    page.locator('#stockRows .symbol-button',has_text='SNDK').click()
    assert page.locator('#detailBody .fan-chart').count()==1
    assert '中确信' in page.locator('#detailBody').inner_text()
    assert '不作为方向性阶段顶/底决策概率' in page.locator('#detailBody').inner_text()
    assert '65.0% / 25.0% / 10.0%' in page.locator('#detailBody').inner_text()
    page.locator('#detailBody select').select_option('-0.015')
    assert '65.0%' in page.locator('#detailBody').inner_text()
    page.locator('#closeDetail').click()
    assert page.locator('#calibrationBody .calibration-chart').count()==1
    assert '多数类基线 54.0%' in page.locator('#calibrationSummary').inner_text()
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
    assert page.locator('#storageQuick, #storageSubFilter, #clearFilters').count()==0
    page.select_option('#bucketFilter','科技与成长主题')
    page.select_option('#groupFilter','存储与内存')
    assert page.locator('#businessTagFilter').count()==0
    assert page.locator('.filter-row #bucketFilter').count()==1
    assert page.locator('#stockRows tr').count()==4
    page.screenshot(path=str(ROOT/'tests/preview-mobile.png'),full_page=False)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 2')
    assert not errors,errors
    checks.append('mobile simplified filters, no whole-page horizontal overflow, no JS errors')
    browser.close()
report={'test_type':'synthetic_ui_only','checks':checks,'check_groups':len(checks),'result':'PASS','financial_backtest':False,'github_live_test':False,'gateway_test':False}
(ROOT/'tests/ui-check-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(f'PASS: {len(checks)} browser check groups; synthetic UI only, no financial/deployment validation.')
