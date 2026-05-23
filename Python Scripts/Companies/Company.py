from dataclasses import asdict, dataclass, field
from typing import List


@dataclass
class CompanyDependencies:
    suppliers: List[str] = field(default_factory=list)
    resources: List[str] = field(default_factory=list)
    infrastructure: List[str] = field(default_factory=list)
    regulatory_exposures: List[str] = field(default_factory=list)
    competitors: List[str] = field(default_factory=list)


@dataclass
class Company:
    ticker: str
    company_name: str
    industry: str
    products: List[str] = field(default_factory=list)
    dependencies: CompanyDependencies = field(default_factory=CompanyDependencies)

    def to_dict(self) -> dict:
        return asdict(self)


APPLE = Company(
    ticker="AAPL",
    company_name="Apple Inc.",
    industry="Consumer Electronics",
    products=["iPhone", "MacBook", "iPad", "Services"],
    dependencies=CompanyDependencies(
        suppliers=["TSMC", "Foxconn", "Samsung Display"],
        resources=["semiconductors", "lithium", "rare earth metals"],
        infrastructure=["shipping", "Asian manufacturing", "app store ecosystem"],
        regulatory_exposures=["China tariffs", "EU digital regulation"],
        competitors=["Samsung", "Google"],
    ),
)
