"""Observational lifecycle hooks: telemetry failure must not change a trading verdict."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from db_helpers.market import record_candidate, record_candidate_snapshot, record_candidate_rejection
from services.pipeline_version import build_pipeline_version

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    stage: str
    reason_code: str
    metrics: dict[str, Any]
    thresholds: dict[str, Any]


def observe(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception:
        LOGGER.exception('Telemetry failed in %s', getattr(function, '__name__', type(function).__name__))
        return None


def begin_candidate(symbol, *, version=None):
    context = record_candidate(symbol, version or build_pipeline_version())
    record_candidate_snapshot(context, 'candidate_created', context, cutoff=context['created_at'])
    from services.catalyst_gates import record_catalyst_gates
    context['catalyst_gates'] = record_catalyst_gates(context)
    return context


def checkpoint(context, stage, payload):
    if context:
        observe(record_candidate_snapshot, context, stage, payload)


def reject(context, stage, reason, *, metrics=None, thresholds=None, shadow=False):
    if context:
        checkpoint(context, stage, {'reason_code': reason, 'metrics': metrics or {},
                                    'thresholds': thresholds or {}, 'shadow': shadow})
        observe(record_candidate_rejection, context,
                GateResult(False, stage, reason, metrics or {}, thresholds or {}), shadow=shadow)


def record_gate(context, name, gate):
    if not context or not isinstance(gate, dict):
        return
    checkpoint(context, name, gate)
    if gate.get('would_allow', gate.get('allowed')) is False:
        config = context['pipeline_version']['gate_config'].get(
            'continuation' if name == 'CONTINUATION' else 'regime', {})
        for reason in gate.get('reason_codes') or ['gate_rejected']:
            reject(context, name, reason, metrics=gate, thresholds=config, shadow=gate.get('allowed') is True)
