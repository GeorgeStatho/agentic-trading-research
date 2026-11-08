"""The collector's forward-evaluation step: challenger evaluations, research outcomes, interim reports.

Cohorts open in the worker and closing marks are polled by the option manager; this step covers
the rest on the collector's schedule, so evidence accumulates without anyone running a report.
"""

from market_db_modules.forward_comparison import refresh_comparison_reports
from market_db_modules.research_outcomes import collect_research_thesis_outcomes
from services.runtime.candidate_lifecycle import observe
from services.evaluation.challenger_evaluation import run_challenger_evaluations
from services.evaluation.forward_evaluation_policy import ForwardEvaluationPolicy
from services.core.time_utils import utc_now_iso


def run_forward_evaluation_cycle(*, clock=utc_now_iso, db_path=None):
    policy = ForwardEvaluationPolicy.from_env()
    result = {'challenger': observe(run_challenger_evaluations, clock=clock, db_path=db_path)}
    if policy.enabled:
        result['research_thesis_outcomes'] = observe(collect_research_thesis_outcomes, as_of=clock(), db_path=db_path)
        result['comparison_reports'] = observe(refresh_comparison_reports, as_of=clock(), db_path=db_path)
    return result
