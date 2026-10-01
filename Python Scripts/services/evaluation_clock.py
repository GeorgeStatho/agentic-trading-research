"""A scoped clock so a frozen decision can be re-finalized as of its own information cutoff.

Live code reads the wall clock. Inside `evaluated_at`, quote-age, scenario-availability and DTE
checks read the frozen cutoff instead, so a decision evaluated hours later is not marked stale.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from catalyst_models import timestamp

_EVALUATED_AT: ContextVar[datetime | None] = ContextVar('evaluated_at', default=None)


def evaluation_now():
    return _EVALUATED_AT.get() or datetime.now(timezone.utc)


@contextmanager
def evaluated_at(at):
    from agent_helpers.deterministic_option_selector.market import selection_date
    moment = timestamp(at)
    if moment is None:
        raise ValueError('An evaluation clock needs a timestamp')
    token = _EVALUATED_AT.set(moment)
    try:
        with selection_date(moment):
            yield moment
    finally:
        _EVALUATED_AT.reset(token)
