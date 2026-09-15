"""Synthetic metadata/LOO/compatibility checks; no real-market effectiveness claims."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import csv, json, sys, unittest
import jsonschema
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from peer_reference import PeerObservation, build_loo_reference
from validate_delivery import validate_seed, validate_dashboard
START=datetime(2026,9,14,20,tzinfo=timezone.utc)
END=START+timedelta(days=1)
CUTOFF=END+timedelta(hours=1)

def row(symbol='PEER-A', issuer='issuer-a', ret=.02, tags=('NAND','SSD')):
    return PeerObservation(symbol,issuer,'storage-memory',tags,ret,1.0,END,END,
                           START-timedelta(days=5),START-timedelta(days=5),START-timedelta(days=1))

def run(rows, **kwargs):
    args=dict(target_issuer_id='target',window_start=START,window_end=END,as_of=CUTOFF)
    args.update(kwargs)
    return build_loo_reference(rows,**args)

class PeerTests(unittest.TestCase):
    def setUp(self):self.rows=[row(),row('PEER-B','issuer-b',-.01),row('PEER-C','issuer-c',.05)]
    def test_equal_weight(self):
        x=run(self.rows);self.assertEqual(x['status'],'validated');self.assertAlmostEqual(x['feature_return'],.02)
    def test_self_and_share_class_exclusion(self):
        x=run(self.rows+[row('TARGET','target',.9),row('TARGET-CLASS-B','target',-.9)])
        self.assertEqual(len(x['peer_symbols']),3);self.assertAlmostEqual(x['feature_return'],.02)
    def test_duplicate_peer_issuer_rejected(self):
        with self.assertRaises(ValueError):run(self.rows+[row('CLASS-B','issuer-a')])
    def test_unknown_target(self):
        x=run(self.rows,target_issuer_id=None);self.assertIsNone(x['feature_return']);self.assertFalse(x['self_excluded'])
    def test_unknown_peer_identity(self):
        x=run(self.rows+[row('UNKNOWN',None)]);self.assertEqual(x['status'],'identity_unverified');self.assertIsNone(x['feature_return'])
    def test_single_peer(self):
        x=run(self.rows[:1]);self.assertEqual(x['status'],'proxy_only');self.assertIsNone(x['feature_return']);self.assertIsNotNone(x['descriptive_return'])
    def test_two_peers(self):self.assertEqual(run(self.rows[:2])['status'],'proxy_only')
    def test_no_peers(self):self.assertEqual(run([])['status'],'no_peers')
    def test_missing_not_zero(self):
        x=run(self.rows+[row('MISSING','missing',None)]);self.assertEqual(x['status'],'insufficient_coverage');self.assertAlmostEqual(x['weight_coverage'],.75)
    def test_future_observation(self):
        bad=replace(row('FUTURE','future'),available_at=CUTOFF+timedelta(seconds=1))
        x=run(self.rows+[bad]);self.assertNotIn('FUTURE',x['peer_symbols']);self.assertEqual(x['status'],'insufficient_coverage')
    def test_future_membership(self):
        bad=replace(row('FUTURE','future'),membership_known_at=END)
        self.assertNotIn('FUTURE',run(self.rows+[bad])['peer_symbols'])
    def test_future_weight(self):
        bad=replace(row('FUTURE','future'),weight_known_at=END)
        self.assertNotIn('FUTURE',run(self.rows+[bad])['peer_symbols'])
    def test_expired_membership(self):
        bad=replace(row('EXPIRED','expired'),effective_to=START)
        self.assertNotIn('EXPIRED',run(self.rows+[bad])['peer_symbols'])
    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):run(self.rows,as_of=CUTOFF.replace(tzinfo=None))
    def test_window_after_prediction(self):
        with self.assertRaises(ValueError):run(self.rows,as_of=START)
    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):run([row(ret=float('nan'))])
    def test_negative_weight_rejected(self):
        with self.assertRaises(ValueError):run([replace(row(),lagged_weight=-1)])
    def test_concentrated_weights_downgraded(self):
        x=run([replace(self.rows[0],lagged_weight=100)]+self.rows[1:]);self.assertEqual(x['status'],'proxy_only')
    def test_subgroup_filters(self):
        x=run([row(tags=('HDD',))]+self.rows[1:],any_tags=('NAND',))
        self.assertEqual(x['peer_symbols'],['PEER-B','PEER-C']);self.assertEqual(x['status'],'proxy_only')
    def test_mismatched_return_window(self):
        x=run(self.rows+[replace(row('STALE','stale'),return_as_of=START)])
        self.assertNotIn('STALE',x['peer_symbols']);self.assertEqual(x['status'],'insufficient_coverage')

class PackageTests(unittest.TestCase):
    def setUp(self):
        with (ROOT/'assets/universe_seed.csv').open(encoding='utf-8-sig',newline='') as f:self.rows=list(csv.DictReader(f))
    def test_seed_integrity(self):self.assertEqual(validate_seed(ROOT/'assets/universe_seed.csv'),100)
    def test_original_symbols_and_buckets_preserved(self):
        baseline=json.loads((ROOT/'tests/baseline-symbols-v1.json').read_text())
        self.assertEqual({r['symbol']:r['coverage_bucket'] for r in self.rows},baseline)
    def test_csv_json_same_metadata(self):
        parsed=[]
        for r in self.rows:
            r=dict(r)
            for k in ['business_tags','industry_tags']:r[k]=[v for v in r[k].split('|') if v]
            parsed.append(r)
        self.assertEqual(parsed,json.loads((ROOT/'assets/universe_seed.json').read_text()))
    def test_subgroup_membership_unique(self):
        r=json.loads((ROOT/'assets/universe_seed.json').read_text())
        for tags,expected in [({'DRAM','HBM'},{'MU'}),({'NAND','SSD'},{'MU','SNDK'}),({'HDD'},{'WDC','STX'})]:
            self.assertEqual({x['symbol'] for x in r if set(x['business_tags'])&tags},expected)
    def test_preview_schema(self):
        d=json.loads((ROOT/'assets/preview-data.json').read_text())
        schema=json.loads((ROOT/'contracts/dashboard.schema.json').read_text())
        jsonschema.Draft202012Validator(schema).validate(d);self.assertEqual(validate_dashboard(d),100)
    def test_no_fabricated_financials(self):
        d=json.loads((ROOT/'assets/preview-data.json').read_text())
        self.assertEqual(d['build_mode'],'preview');self.assertIsNone(d['storage_rotation'])
        for r in d['records']:
            self.assertIsNone(r['reference_price'])
            for m in r['metrics'].values():
                self.assertEqual(m['status'],'uncalibrated')
                for k,v in m.items():
                    if k!='status':self.assertIsNone(v)
    def test_schema_legacy_records_accepted(self):
        schema=json.loads((ROOT/'contracts/dashboard.schema.json').read_text())
        old={'build_mode':'preview','records':[{'symbol':'OLD-TEST','metrics':{str(h):{'status':'uncalibrated'} for h in [5,10,21]}}]}
        jsonschema.Draft202012Validator(schema).validate(old)
    def test_required_original_research_sections(self):
        s=(ROOT/'references/model-spec.md').read_text()
        for term in ['市场情绪与持续时间','行业轮动','个股独立表现','结构价带','波动、期权与事件','凯利与执行','共同路径分布','冷启动与降级']:self.assertIn(term,s)
        s=(ROOT/'assets/dashboard.html').read_text()
        for term in ['市场与板块是否同步','确认与失效条件','基本面与事件证据','尾部风险与执行','首次触达路径','数据与模型']:self.assertIn(term,s)

if __name__=='__main__':unittest.main()
