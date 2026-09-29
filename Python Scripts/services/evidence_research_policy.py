"""Non-secret foundation configuration; these budgets do not start research workers."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import os
from pathlib import Path

from research_foundation_contracts import OBSERVATION_PROTOCOL, SCHEMA_VERSION, nonnegative
from services.research_contract import fingerprint


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class EvidenceResearchPolicy:
    max_rounds: int = 2
    max_tool_calls: int = 6
    max_seconds: int = 90
    daily_cost_budget: float = 0

    def __post_init__(self):
        nonnegative(self.max_rounds, self.max_tool_calls, self.max_seconds, self.daily_cost_budget)
        if min(self.max_rounds, self.max_tool_calls, self.max_seconds) < 1:
            raise ValueError("Research request limits must be positive")

    @classmethod
    def from_env(cls):
        return cls(max_rounds=int(os.getenv("RESEARCH_MAX_ROUNDS", "2")),
                   max_tool_calls=int(os.getenv("RESEARCH_MAX_TOOL_CALLS", "6")),
                   max_seconds=int(os.getenv("RESEARCH_MAX_SECONDS", "90")),
                   daily_cost_budget=float(os.getenv("RESEARCH_DAILY_COST_BUDGET", "0")))


def foundation_manifest():
    from services.contract_policy import ContractPolicy
    from services.pricing_policy import PricingPolicy
    from services.research_versions import evaluation_version
    sources = [ROOT / "Data/research_foundation_contracts.py"]
    sources.extend((ROOT / "Data/market_db_modules").glob("*foundation*.*"))
    sources.extend(ROOT / name for name in (
        "Data/market_db_modules/company_evidence.py", "Data/market_db_modules/research_observations.py"))
    adapters = sorted((ROOT / "Python Scripts/Webscraping/company_evidence").rglob("*.py"))
    sources.extend(adapters)
    sources.extend((ROOT / "Python Scripts/services").glob("company_evidence*.py"))
    sources.extend((ROOT / "Data/market_db_modules").glob("company_collection*.*"))
    sources.append(ROOT / "Python Scripts/agentCallers/agent_builders/company_evidence_payload.py")
    sources.extend((ROOT / "Data/market_db_modules").glob("discovery*.*"))
    sources.extend((ROOT / "Python Scripts/services").glob("discovery*.py"))
    sources.append(ROOT / "Python Scripts/services/event_discovery.py")
    hashes = {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
              for path in sorted(sources) if path.is_file()}
    from services.company_evidence_policy import provider_manifest
    provider_config = {**provider_manifest(), "adapters": [str(p.relative_to(ROOT)) for p in adapters]}
    from services.discovery_policy import DiscoveryPolicy
    provider_config["discovery"] = DiscoveryPolicy.from_env().manifest()
    budgets = asdict(EvidenceResearchPolicy.from_env())
    economics = {"pricing": asdict(PricingPolicy.from_env()), "contracts": asdict(ContractPolicy.from_env())}
    return {"schema_version": SCHEMA_VERSION, "observation_protocol": OBSERVATION_PROTOCOL,
            "evaluation_version": evaluation_version(),
            "source_hashes": hashes, "provider_config": provider_config,
            "provider_config_hash": fingerprint(provider_config), "research_budgets": budgets,
            "research_budget_hash": fingerprint(budgets), "economics_assumptions": economics,
            "economics_assumptions_hash": fingerprint(economics)}
