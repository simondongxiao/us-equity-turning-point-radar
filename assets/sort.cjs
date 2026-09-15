(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RadarUtils = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';
  function metric(record, field, horizon) {
    const item = record.metrics && record.metrics[String(horizon)];
    if (!item || !['calibrated','calibrated_low_confidence'].includes(item.status)) return null;
    const value = item[field];
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }
  function compare(a, b, field, direction, horizon) {
    const x = metric(a, field, horizon), y = metric(b, field, horizon);
    if (x === null && y !== null) return 1;
    if (x !== null && y === null) return -1;
    if (x !== null && y !== null && x !== y) return (x-y)*(direction === 'asc' ? 1 : -1);
    return String(a.symbol).localeCompare(String(b.symbol), 'en');
  }
  function sorted(records, field, direction, horizon) {
    if (!['opportunity_value','risk_value'].includes(field)) throw new Error('Invalid sort field');
    if (!['asc','desc'].includes(direction)) throw new Error('Invalid direction');
    return [...records].sort((a,b)=>compare(a,b,field,direction,horizon));
  }
  return { metric, compare, sorted };
});
