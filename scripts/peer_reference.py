"""Point-in-time, single-period leave-one-issuer-out reference utility.

Not a trained model. Inputs must be real, identity-resolved observations in production.
`validated` means arithmetic/data gates passed, NOT calibrated prediction accuracy.
For a historical factor series, call independently at each actual historical interval.
"""
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable, Optional


@dataclass(frozen=True)
class PeerObservation:
    symbol: str
    issuer_id: Optional[str]
    group_id: str
    business_tags: tuple[str, ...]
    return_value: Optional[float]
    lagged_weight: float
    return_as_of: datetime
    available_at: datetime
    membership_known_at: datetime
    effective_from: datetime
    weight_known_at: datetime
    effective_to: Optional[datetime] = None


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('All timestamps must be timezone-aware datetime objects')


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build_loo_reference(
    observations: Iterable[PeerObservation], *, target_issuer_id: Optional[str],
    window_start: datetime, window_end: datetime, as_of: datetime,
    group_id: str = 'storage-memory', any_tags: tuple[str, ...] = (),
    min_peers: int = 3, min_effective_peers: float = 2.5,
    min_weight_coverage: float = 0.8,
) -> dict:
    """Return a descriptive reference plus a gated feature; never invent missing returns.

    Membership and lagged weights must be known/effective at window_start. All
    returns must refer to window_end and be available by as_of. A single target
    issuer (including its other share classes) is removed before aggregation.
    Upstream must validate business tags, prices, corporate actions and currencies.
    """
    for t in (window_start, window_end, as_of):
        _aware(t)
    if not window_start < window_end <= as_of:
        raise ValueError('Require window_start < window_end <= prediction cutoff')
    if isinstance(min_peers, bool) or not isinstance(min_peers, int) or min_peers < 3:
        raise ValueError('Multi-name confirmation requires at least three peers')
    if not _finite(min_effective_peers) or min_effective_peers <= 0:
        raise ValueError('Invalid effective-peer threshold')
    if not _finite(min_weight_coverage) or not 0 < min_weight_coverage <= 1:
        raise ValueError('Invalid weight-coverage threshold')
    base = {'status': 'identity_unverified', 'self_excluded': False,
            'target_issuer_id': target_issuer_id, 'peer_symbols': [], 'peer_count': 0,
            'effective_n': None, 'weight_coverage': None, 'descriptive_return': None,
            'feature_return': None, 'weights': {}, 'excluded': [],
            'as_of': as_of.isoformat(), 'window_start': window_start.isoformat(),
            'window_end': window_end.isoformat(), 'note': 'No predictive calibration is implied.'}
    if not isinstance(target_issuer_id, str) or not target_issuer_id.strip():
        base['note'] = 'Target issuer identity missing; do not claim self-exclusion.'
        return base
    accepted, denominator, seen = [], 0.0, set()
    identity_gap = False
    for row in observations:
        if not isinstance(row, PeerObservation):
            raise TypeError('Expected PeerObservation')
        if row.group_id != group_id or (any_tags and not set(any_tags).intersection(row.business_tags)):
            continue
        for t in (row.return_as_of, row.available_at, row.membership_known_at,
                  row.effective_from, row.weight_known_at):
            _aware(t)
        if row.effective_to is not None:
            _aware(row.effective_to)
        reason = None
        if row.membership_known_at > window_start or row.effective_from > window_start:
            reason = 'membership_not_known_or_not_effective_at_start'
        elif row.effective_to is not None and row.effective_to <= window_start:
            reason = 'membership_expired'
        elif row.weight_known_at > window_start:
            reason = 'future_weight'
        elif not isinstance(row.issuer_id, str) or not row.issuer_id.strip():
            reason, identity_gap = 'issuer_identity_missing', True
        elif row.issuer_id == target_issuer_id:
            reason = 'target_issuer_excluded'
        if reason:
            base['excluded'].append({'symbol': row.symbol, 'reason': reason})
            continue
        if row.issuer_id in seen:
            raise ValueError(f'Duplicate peer issuer {row.issuer_id}; resolve share classes upstream')
        seen.add(row.issuer_id)
        if not _finite(row.lagged_weight) or row.lagged_weight <= 0:
            raise ValueError('Lagged weights must be finite and positive')
        denominator += row.lagged_weight
        if row.available_at > as_of:
            reason = 'return_not_available_by_cutoff'
        elif row.return_as_of != window_end:
            reason = 'return_window_mismatch'
        elif row.return_value is None:
            reason = 'return_missing'
        elif not _finite(row.return_value) or row.return_value < -1:
            raise ValueError('Simple return must be finite and >= -1; missing is None')
        if reason:
            base['excluded'].append({'symbol': row.symbol, 'reason': reason})
            continue
        accepted.append(row)
    base['self_excluded'] = not identity_gap
    if not accepted:
        base['status'] = 'identity_unverified' if identity_gap else 'no_peers'
        base['weight_coverage'] = 0.0 if denominator else None
        return base
    numerator = sum(r.lagged_weight for r in accepted)
    weights = {r.symbol: r.lagged_weight / numerator for r in accepted}
    if len(weights) != len(accepted):
        raise ValueError('Duplicate symbol with differing issuer identities')
    effective = 1.0 / sum(w*w for w in weights.values())
    coverage = numerator / denominator
    descriptive = sum(weights[r.symbol] * r.return_value for r in accepted)
    base.update(peer_symbols=[r.symbol for r in accepted], peer_count=len(accepted),
                effective_n=effective, weight_coverage=coverage,
                descriptive_return=descriptive, weights=weights)
    if identity_gap:
        status = 'identity_unverified'
    elif len(accepted) < min_peers or effective < min_effective_peers:
        status = 'proxy_only'
    elif coverage < min_weight_coverage:
        status = 'insufficient_coverage'
    else:
        status = 'validated'
    base['status'] = status
    base['feature_return'] = descriptive if status == 'validated' else None
    return base
