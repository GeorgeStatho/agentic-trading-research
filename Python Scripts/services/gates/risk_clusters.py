"""Maintained cluster caps, with the same session reservation as sector exposure."""
from services.gates.contract_policy import ContractPolicy
from market_db_modules.contract_research import get_risk_cluster


def evaluate_cluster(*, cluster, option_symbol, estimated_cost, session, policy=None):
    policy = policy or ContractPolicy.from_env()
    current = session.cluster_exposure_by_key.get(cluster, 0.0) if cluster else None
    symbols = session.cluster_option_symbols.get(cluster, set()) if cluster else set()
    projected_count = len(symbols | {option_symbol})
    projected_exposure = current + estimated_cost if current is not None else None
    cap = session.max_deployable_buying_power * policy.maximum_cluster_exposure_fraction
    blocked = bool(cluster) and (projected_count > policy.maximum_cluster_positions or projected_exposure > cap)
    return {'stage': 'RISK_CLUSTER', 'risk_cluster_id': cluster, 'would_allow': not blocked if cluster else None,
            'allowed': not blocked or policy.cluster_mode != 'enforce', 'enforcement_mode': policy.cluster_mode,
            'reason_codes': ['portfolio_risk_cluster_limit'] if blocked else ['unmapped_cluster'] if not cluster else [],
            'metrics': {'current_exposure': current, 'projected_exposure': projected_exposure,
                        'projected_positions': projected_count if cluster else None},
            'thresholds': {'maximum_positions': policy.maximum_cluster_positions, 'maximum_exposure': cap}}
