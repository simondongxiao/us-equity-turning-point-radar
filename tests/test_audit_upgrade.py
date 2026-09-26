"""Leakage and preservation checks for the shadow evaluation."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.audit_upgrade import freeze, split_at_origin, features, labels, metrics, conditional_bands, path_fan, explain_and_stress, fit, predict
from scripts.check_publish_time import check


class AuditTests(unittest.TestCase):
    def test_old_build_cannot_replace_new_website(self):
        with self.assertRaises(ValueError):check({'as_of':'2026-09-14'},{'as_of':'2026-09-24'})
        check({'as_of':'2026-09-25'},{'as_of':'2026-09-24'})
    def test_frozen_conflict(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'outputs') as tmp:
            p=Path(tmp)/'forecast.json'
            sha=freeze(p,{'p':.3})
            self.assertEqual(sha,freeze(p,{'p':.3}))
            with self.assertRaises(ValueError): freeze(p,{'p':.9})
            self.assertEqual(p.read_text(),'{"p":0.3}')

    def test_maturity_purges_both_boundaries(self):
        rows=pd.DataFrame({'date':pd.to_datetime(['2024-01-01','2024-06-28','2024-07-02','2024-12-29']),
                           'label_end':pd.to_datetime(['2024-01-10','2024-07-03','2024-07-15','2025-01-03'])})
        train,cal=split_at_origin(rows,pd.Timestamp('2025-01-01'))
        self.assertEqual(len(train),1);self.assertEqual(len(cal),1)
        self.assertTrue((train.label_end<pd.Timestamp('2024-07-01')).all())
        self.assertTrue((cal.label_end<pd.Timestamp('2025-01-01')).all())

    def test_features_cannot_see_future(self):
        dates=pd.bdate_range('2020-01-01',periods=180)
        rng=np.random.default_rng(7); c=100*np.exp(np.cumsum(rng.normal(0,.01,len(dates))))
        frame=pd.DataFrame({'close':c,'open':c,'high':c*1.01,'low':c*.99},index=dates)
        before=features(frame,{'SPY':frame,'QQQ':frame})
        altered=frame.copy();altered.iloc[140:]*=10
        after=features(altered,{'SPY':altered,'QQQ':altered})
        pd.testing.assert_frame_equal(before.iloc[:140],after.iloc[:140])

    def test_independent_event_calibration(self):
        rows=[{'date':'2025-01-01','p_bottom':.8,'p_top':.9,'bottom':1,'top':1}]
        m=metrics(rows)
        self.assertEqual(m['bottom']['high_precision'],1)
        self.assertEqual(m['top']['high_precision'],1)

    def test_bands_follow_event_mass_and_scale(self):
        history=pd.DataFrame([{'joint':c,'atr_pct':.02,'low_ratio':.95,'high_ratio':1.06} for c in ('00','01','10','11') for _ in range(25)])
        bands=conditional_bands(history,np.array([[1,0,0,0],[0,0,0,1]]),np.array([.02,.04]))
        self.assertTrue(np.isnan(bands['bottom'][0]).all())
        np.testing.assert_allclose(bands['bottom'][1],[.90,.90,.90])
        np.testing.assert_allclose(bands['top'][1],[1.12,1.12,1.12])

    def test_fan_attribution_and_stress_are_separate(self):
        rows=[]
        for i,state in enumerate(('00','01','10','11')*100):
            row={c:float((i%17)/17) for c in ['ret5','ret20','ma20','ma50','atr_pct','rv20','market5','market20','qqq5','sox5','vix','beta','residual5']}
            row.update({'joint':state,'path_ratios':[1.0,1.01],'atr_pct':.02})
            rows.append(row)
        history=pd.DataFrame(rows);bundle=fit(history.iloc[:300],history.iloc[300:],['ret5','ret20','ma20','ma50','atr_pct','rv20','market5','market20','qqq5','sox5','vix','beta','residual5'])
        current=history.iloc[:1]
        probabilities=predict(bundle,current)
        fan=path_fan(history,probabilities,current.atr_pct,2)[0]
        attribution,stress=explain_and_stress(bundle,current)
        self.assertEqual(fan['sessions'],[0,1,2])
        self.assertEqual({x['group'] for x in attribution[0]},{'价量结构','市场/指数','个股Beta/残差'})
        self.assertEqual([x['market_shock'] for x in stress[0]],[-.03,-.015,0,.015,.03])


if __name__=='__main__': unittest.main()
