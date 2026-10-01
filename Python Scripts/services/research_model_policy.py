"""Provider selection and bounded text-generation accounting, without loading credentials."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os

from market_db_modules.research_runtime import ResearchBudgetExceeded


PLANNER_PREFIX = 'RESEARCH_PLANNER'
THESIS_PREFIX = 'RESEARCH_THESIS'
# Like the other agent stages, research models fall back to the strategist's configuration.
STAGE_MODEL_FALLBACKS = ('STRATEGIST_MODEL', 'MACRO_NEWS_MODEL', 'WORLD_NEWS_MODEL')
STAGE_HOST_FALLBACKS = ('STRATEGIST_OLLAMA_HOST', 'MACRO_NEWS_OLLAMA_HOST', 'WORLD_NEWS_OLLAMA_HOST')
DEFAULT_OLLAMA_HOST = 'http://127.0.0.1:11434'
THESIS_RATE_SETTINGS = ('INPUT_USD_PER_MILLION', 'OUTPUT_USD_PER_MILLION')


def _first_setting(names, default=None):
    return next((os.getenv(name) for name in names if os.getenv(name)), default)


def configured_provider():
    return (os.getenv('RESEARCH_PLANNER_PROVIDER') or os.getenv('LLM_PROVIDER', 'ollama')).strip().lower()


def thesis_provider():
    """The thesis writer follows the strategist/manager convention: one global LLM_PROVIDER."""
    return os.getenv('LLM_PROVIDER', 'ollama').strip().lower()


def thesis_host():
    return _first_setting(('RESEARCH_THESIS_OLLAMA_HOST', *STAGE_HOST_FALLBACKS), DEFAULT_OLLAMA_HOST)


def thesis_rates_configured():
    return any(os.getenv(f'{THESIS_PREFIX}_{name}') for name in THESIS_RATE_SETTINGS)


def configured_model(provider, override='RESEARCH_PLANNER_MODEL'):
    configured = _first_setting((override, *STAGE_MODEL_FALLBACKS))
    if provider == 'vertex':
        if not configured or configured in {'llama3.1', 'world-news-sectors'}:
            return os.getenv('VERTEX_DEFAULT_MODEL', 'gemini-2.5-flash')
    return configured or ''


@dataclass(frozen=True)
class ResearchModelPolicy:
    max_input_tokens: int = 131072
    max_output_tokens: int = 4096
    thinking_budget: int = 1024
    thinking_level: str = ""
    thinking_token_reservation: int = 32768
    input_usd_per_million: float = 0
    output_usd_per_million: float = 0

    def __post_init__(self):
        for number in (self.max_input_tokens, self.max_output_tokens):
            if type(number) is not int or number < 1:
                raise ValueError('Research token ceilings must be positive integers')
        if type(self.thinking_budget) is not int or self.thinking_budget < 0:
            raise ValueError('Research thinking budget must be explicit and nonnegative')
        if self.thinking_level not in {'', 'minimal', 'low', 'medium', 'high'}:
            raise ValueError('Invalid research thinking level')
        if type(self.thinking_token_reservation) is not int or self.thinking_token_reservation < self.thinking_budget:
            raise ValueError('Thinking reservation must cover the configured thinking budget')
        for rate in (self.input_usd_per_million, self.output_usd_per_million):
            if isinstance(rate, bool) or not math.isfinite(rate) or rate < 0:
                raise ValueError('Research token rates must be finite and nonnegative')

    @classmethod
    def from_env(cls, prefix=PLANNER_PREFIX):
        """Read one stage's limits; a non-planner stage falls back to each planner setting."""
        def setting(name, default):
            own = os.getenv(f'{prefix}_{name}')
            if prefix != PLANNER_PREFIX and own:
                return own
            return os.getenv(f'{PLANNER_PREFIX}_{name}', default)

        return cls(max_input_tokens=int(setting('MAX_INPUT_TOKENS', '131072')),
                   max_output_tokens=int(setting('MAX_OUTPUT_TOKENS', '4096')),
                   thinking_budget=int(setting('THINKING_BUDGET', '1024')),
                   thinking_level=setting('THINKING_LEVEL', '').strip().lower(),
                   thinking_token_reservation=int(setting('THINKING_TOKEN_RESERVATION', '32768')),
                   input_usd_per_million=float(setting('INPUT_USD_PER_MILLION', '0')),
                   output_usd_per_million=float(setting('OUTPUT_USD_PER_MILLION', '0')))

    def reservation(self, provider, messages, schema):
        # Text-only UTF-8 bytes plus serialization/schema overhead deliberately overreserve
        # compared with normal tokenization; this is not a provider token-count measurement.
        input_bound = len(json.dumps({'messages': messages, 'schema': schema}, ensure_ascii=False).encode()) + 1024
        if input_bound > self.max_input_tokens:
            raise ResearchBudgetExceeded('planner_input_token_budget')
        if provider == 'vertex' and min(self.input_usd_per_million, self.output_usd_per_million) <= 0:
            raise ValueError('Vertex research requires explicit positive input/output token rates')
        output_bound = self.max_output_tokens + (self.thinking_token_reservation if provider == 'vertex' else 0)
        cost = ((input_bound * self.input_usd_per_million + output_bound * self.output_usd_per_million)
                / 1_000_000 if provider == 'vertex' else 0)
        if not math.isfinite(cost):
            raise ValueError('Research reservation cost is not finite')
        return {'provider': provider, 'input_tokens_reserved': input_bound, 'output_tokens_reserved': output_bound,
                'cost_reserved_usd': cost, 'rates': {'input': self.input_usd_per_million,
                                                   'output': self.output_usd_per_million}}

    def usage_cost(self, usage):
        required = ('prompt_token_count', 'candidates_token_count', 'thoughts_token_count')
        if any(type(usage.get(key)) is not int or usage[key] < 0 for key in required):
            return None
        return (usage['prompt_token_count'] * self.input_usd_per_million
                + (usage['candidates_token_count'] + usage['thoughts_token_count'])
                * self.output_usd_per_million) / 1_000_000


def planner_manifest():
    provider = configured_provider()
    return {'provider': provider, 'model': configured_model(provider),
            'project': os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('VERTEX_PROJECT') or os.getenv('GCP_PROJECT'),
            'location': os.getenv('GOOGLE_CLOUD_LOCATION') or os.getenv('VERTEX_LOCATION', 'global'),
            'endpoint_hash': sha256(os.getenv('RESEARCH_PLANNER_HOST', 'http://localhost:11434').encode()).hexdigest(),
            'limits_and_rates': asdict(ResearchModelPolicy.from_env())}


def thesis_writer_manifest():
    provider = thesis_provider()
    return {'provider': provider, 'model': configured_model(provider, override='RESEARCH_THESIS_MODEL'),
            'project': os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('VERTEX_PROJECT') or os.getenv('GCP_PROJECT'),
            'location': os.getenv('GOOGLE_CLOUD_LOCATION') or os.getenv('VERTEX_LOCATION', 'global'),
            'endpoint_hash': sha256(thesis_host().encode()).hexdigest(),
            'limits_and_rates': asdict(ResearchModelPolicy.from_env(THESIS_PREFIX))}
