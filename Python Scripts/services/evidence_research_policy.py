"""Non-secret foundation configuration; shadow investigative research budgets."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import os
from pathlib import Path

from research_foundation_contracts import OBSERVATION_PROTOCOL, SCHEMA_VERSION, nonnegative
from services.research_contract import fingerprint
from services.company_evidence_policy import provider_manifest
from services.contract_policy import ContractPolicy
from services.env_parsing import env_flag
from services.discovery_policy import DiscoveryPolicy
from services.pricing_policy import PricingPolicy
from services.research_model_policy import planner_manifest, thesis_writer_manifest
from services.research_versions import evaluation_version
from services.trade_economics_policy import TradeEconomicsPolicy


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class EvidenceResearchPolicy:
    mode: str = "disabled"
    daily_planner_calls: int = 100
    daily_retrieval_calls: int = 600
    max_planner_attempts: int = 2
    retry_seconds: int = 1
    max_rounds: int = 2
    max_tool_calls: int = 6
    max_seconds: int = 90
    daily_cost_budget: float = 0
    search_enabled: bool = False

    def __post_init__(self):
        if self.mode not in {"disabled", "shadow"}:
            raise ValueError("Research supports disabled or shadow mode only")
        if type(self.search_enabled) is not bool:
            raise ValueError("Research search flag must be a boolean")
        for value in (self.max_rounds, self.max_tool_calls, self.max_seconds,
                      self.daily_planner_calls, self.daily_retrieval_calls, self.max_planner_attempts, self.retry_seconds):
            if type(value) is not int or value < 1:
                raise ValueError("Research call/time allowances must be positive integers")
        nonnegative(self.max_rounds, self.max_tool_calls, self.max_seconds, self.daily_cost_budget)

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv("RESEARCH_MODE", "disabled"),
                   daily_planner_calls=int(os.getenv("RESEARCH_DAILY_PLANNER_CALLS", "100")),
                   daily_retrieval_calls=int(os.getenv("RESEARCH_DAILY_RETRIEVAL_CALLS", "600")),
                   max_planner_attempts=int(os.getenv("RESEARCH_PLANNER_MAX_ATTEMPTS", "2")),
                   retry_seconds=int(os.getenv("RESEARCH_PLANNER_RETRY_SECONDS", "1")),
                   max_rounds=int(os.getenv("RESEARCH_MAX_ROUNDS", "2")),
                   max_tool_calls=int(os.getenv("RESEARCH_MAX_TOOL_CALLS", "6")),
                   max_seconds=int(os.getenv("RESEARCH_MAX_SECONDS", "90")),
                   daily_cost_budget=float(os.getenv("RESEARCH_DAILY_COST_BUDGET", "0")),
                   search_enabled=env_flag("RESEARCH_SEARCH_ENABLED", False))


def foundation_manifest():
    sources = [ROOT / "Data/research_foundation_contracts.py"]
    sources.extend((ROOT / "Data/market_db_modules").glob("*foundation*.*"))
    sources.extend(ROOT / name for name in (
        "Data/market_db_modules/company_evidence.py", "Data/market_db_modules/research_observations.py",
        "Data/market_db_modules/evidence_refresh.py", "Data/news_db_modules/impacts.py",
        "Data/market_db_modules/research_work.py", "Data/news_db_modules/provenance.py",
        "Python Scripts/services/company_interest.py"))
    adapters = sorted((ROOT / "Python Scripts/Webscraping/company_evidence").rglob("*.py"))
    sources.extend(adapters)
    sources.extend((ROOT / "Python Scripts/services").glob("company_evidence*.py"))
    sources.extend((ROOT / "Data/market_db_modules").glob("company_collection*.*"))
    sources.extend((ROOT / "Data/market_db_modules").glob("company_interest*.*"))
    sources.append(ROOT / "Python Scripts/agentCallers/agent_builders/company_evidence_payload.py")
    sources.extend((ROOT / "Data/market_db_modules").glob("discovery*.*"))
    sources.extend((ROOT / "Python Scripts/services").glob("discovery*.py"))
    sources.append(ROOT / "Python Scripts/services/event_discovery.py")
    sources.extend((ROOT / "Data/market_db_modules").glob("research_runtime*.*"))
    sources.extend((ROOT / "Data/market_db_modules").glob("evidence_thesis*.*"))
    sources.extend((ROOT / "Python Scripts/services").glob("evidence_thesis*.py"))
    sources.extend((ROOT / "Python Scripts/agentCallers/agent_stages").glob("*thesis*.py"))
    sources.extend((ROOT / "Python Scripts/services").glob("research_*.py"))
    sources.extend((ROOT / "Python Scripts/agentCallers/agent_stages").glob("research_planner*.py"))
    hashes = {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
              for path in sorted(sources) if path.is_file()}
    provider_config = {**provider_manifest(), "adapters": [str(p.relative_to(ROOT)) for p in adapters]}
    provider_config["discovery"] = DiscoveryPolicy.from_env().manifest()
    provider_config["research_planner"] = planner_manifest()
    provider_config["research_thesis_writer"] = thesis_writer_manifest()
    budgets = asdict(EvidenceResearchPolicy.from_env())
    economics = {"pricing": asdict(PricingPolicy.from_env()), "contracts": asdict(ContractPolicy.from_env()),
                 "trade_economics": TradeEconomicsPolicy.from_env().assumptions()}
    return {"schema_version": SCHEMA_VERSION, "observation_protocol": OBSERVATION_PROTOCOL,
            "evaluation_version": evaluation_version(),
            "source_hashes": hashes, "provider_config": provider_config,
            "provider_config_hash": fingerprint(provider_config), "research_budgets": budgets,
            "research_budget_hash": fingerprint(budgets), "economics_assumptions": economics,
            "economics_assumptions_hash": fingerprint(economics)}
