'use strict';
const assert = require('node:assert/strict');
const { sorted } = require('../assets/sort.cjs');
function r(symbol,o,k,status='calibrated') {
 return {symbol,metrics:{'10':{status,opportunity_value:o,risk_value:k},'5':{status,opportunity_value:k,risk_value:o}}};
}
const data=[r('TEST-C',2,.09),r('TEST-A',10,.01),r('TEST-B',-2,.2),r('TEST-X',null,null),r('TEST-Z',99,0,'stale')];
const ids=(x)=>x.map(r=>r.symbol);
assert.deepEqual(ids(sorted(data,'opportunity_value','desc',10)),['TEST-A','TEST-C','TEST-B','TEST-X','TEST-Z']);
assert.deepEqual(ids(sorted(data,'opportunity_value','asc',10)),['TEST-B','TEST-C','TEST-A','TEST-X','TEST-Z']);
assert.deepEqual(ids(sorted(data,'risk_value','desc',10)),['TEST-B','TEST-C','TEST-A','TEST-X','TEST-Z']);
assert.deepEqual(ids(sorted(data,'risk_value','asc',10)),['TEST-A','TEST-C','TEST-B','TEST-X','TEST-Z']);
assert.deepEqual(ids(sorted(data,'opportunity_value','desc',5)),['TEST-B','TEST-C','TEST-A','TEST-X','TEST-Z']);
assert.deepEqual(ids(sorted([r('TEST-B',0,.1),r('TEST-A',0,.1)],'opportunity_value','desc',10)),['TEST-A','TEST-B']);
assert.deepEqual(ids(sorted([r('TEST-B',0,.1),r('TEST-A',null,null)],'opportunity_value','asc',10)),['TEST-B','TEST-A']);
assert.deepEqual(ids(data),['TEST-C','TEST-A','TEST-B','TEST-X','TEST-Z']);
assert.throws(()=>sorted(data,'symbol','asc',10));
console.log('PASS: 9 synthetic sorting assertions. This is not a financial backtest.');
