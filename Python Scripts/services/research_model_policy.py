"""Provider selection and bounded text-generation accounting, without loading credentials."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os

from market_db_modules.research_runtime import ResearchBudgetExceeded
from services.gemini_models import THINKING_LEVELS, default_vertex_model
from services.settings import get_settings


PLANNER_PREFIX = 'RESEARCH_PLANNER'
THESIS_PREFIX = 'RESEARCH_THESIS'
# Like the other agent stages, research models fall back to the strategist's configuration.
STAGE_MODEL_FALLBACKS = ('STRATEGIST_MODEL', 'MACRO_NEWS_MODEL', 'WORLD_NEWS_MODEL')
STAGE_HOST_FALLBACKS = ('STRATEGIST_OLLAMA_HOST', 'MACRO_NEWS_OLLAMA_HOST', 'WORLD_NEWS_OLLAMA_HOST')
# ...and so does the provider, so an inherited model name is never sent to another provider.
STAGE_PROVIDER_FALLBACKS = ('STRATEGIST_PROVIDER', 'LLM_PROVIDER')
DEFAULT_OLLAMA_HOST = 'http://127.0.0.1:11434'
THESIS_RATE_SETTINGS = ('INPUT_USD_PER_MILLION', 'OUTPUT_USD_PER_MILLION')
SUPPORTED_PROVIDERS = frozenset({'ollama', 'vertex', 'gemini', 'openai', 'openai_compatible', 'llamacpp'})
GOOGLE_PROVIDERS = frozenset({'vertex', 'gemini'})
# Always billed, so a call needs explicit token rates. Other hosted providers may be free (zero rates).
RATES_REQUIRED = frozenset({'vertex', 'openai'})
LOCAL_PROVIDERS = frozenset({'ollama', 'llamacpp'})
OPENAI_ENDPOINT = 'https://api.openai.com/v1'


def _first_setting(names, default=None):
    return next((os.getenv(name) for name in names if os.getenv(name)), default)


def configured_provider():
    return _first_setting(('RESEARCH_PLANNER_PROVIDER', *STAGE_PROVIDER_FALLBACKS), 'ollama').strip().lower()


def thesis_provider():
    """Like the model, the provider falls back to the strategist's, then LLM_PROVIDER."""
    return _first_setting(('RESEARCH_THESIS_PROVIDER', *STAGE_PROVIDER_FALLBACKS), 'ollama').strip().lower()


def research_endpoint(provider, host):
    """The URL a provider's requests go to, or None where the SDK picks it (Vertex, Gemini API)."""
    settings = get_settings()
    return {'ollama': host, 'openai': OPENAI_ENDPOINT, 'llamacpp': settings.llamacpp.base_url,
            'openai_compatible': settings.openai_compatible.base_url}.get(provider)


def manifest_endpoint_hash(provider, host):
    # Vertex/Gemini keep hashing the configured host, as manifests always have: the manifest is part of a
    # champion's identity, so an unchanged configuration must keep producing the same value.
    return sha256((research_endpoint(provider, host) or host).encode()).hexdigest()


def planner_host():
    return os.getenv('RESEARCH_PLANNER_HOST', 'http://localhost:11434')


def thesis_host():
    return _first_setting(('RESEARCH_THESIS_OLLAMA_HOST', *STAGE_HOST_FALLBACKS), DEFAULT_OLLAMA_HOST)


def thesis_rates_configured():
    return any(os.getenv(f'{THESIS_PREFIX}_{name}') for name in THESIS_RATE_SETTINGS)


def configured_model(provider, override='RESEARCH_PLANNER_MODEL'):
    configured = _first_setting((override, *STAGE_MODEL_FALLBACKS))
    if provider in GOOGLE_PROVIDERS:
        if not configured or configured in {'llama3.1', 'world-news-sectors'}:
            return default_vertex_model()
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
        # Model support varies (Gemini 3.8 has no minimal); research_planner sends the nearest supported level.
        if self.thinking_level and self.thinking_level not in THINKING_LEVELS:
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

    def billed(self, provider):
        """Whether calls cost money: always for vertex/openai, never locally, and otherwise when rates are set."""
        if provider in LOCAL_PROVIDERS:
            return False
        return provider in RATES_REQUIRED or min(self.input_usd_per_million, self.output_usd_per_million) > 0

    def output_bound(self, provider):
        # Ollama has no separate thinking allowance; every other provider can think past the answer limit.
        return self.max_output_tokens + (0 if provider == 'ollama' else self.thinking_token_reservation)

    def request_output_tokens(self, provider):
        """The output cap sent with each request. Gemini caps the answer and reports thinking separately;
        Chat Completions and Responses count reasoning inside the cap, so it covers the whole reservation."""
        return self.max_output_tokens if provider in GOOGLE_PROVIDERS | {'ollama'} else self.output_bound(provider)

    def reservation(self, provider, messages, schema):
        # Text-only UTF-8 bytes plus serialization/schema overhead deliberately overreserve
        # compared with normal tokenization; this is not a provider token-count measurement.
        input_bound = len(json.dumps({'messages': messages, 'schema': schema}, ensure_ascii=False).encode()) + 1024
        if input_bound > self.max_input_tokens:
            raise ResearchBudgetExceeded('planner_input_token_budget')
        if provider in RATES_REQUIRED and min(self.input_usd_per_million, self.output_usd_per_million) <= 0:
            raise ValueError(f'{provider} research requires explicit positive input/output token rates')
        billed = self.billed(provider)
        output_bound = self.output_bound(provider)
        cost = ((input_bound * self.input_usd_per_million + output_bound * self.output_usd_per_million)
                / 1_000_000 if billed else 0)
        if not math.isfinite(cost):
            raise ValueError('Research reservation cost is not finite')
        return {'provider': provider, 'billed': billed, 'input_tokens_reserved': input_bound,
                'output_tokens_reserved': output_bound, 'cost_reserved_usd': cost,
                'rates': {'input': self.input_usd_per_million, 'output': self.output_usd_per_million}}

    def usage_cost(self, usage):
        required = ('prompt_token_count', 'candidates_token_count', 'thoughts_token_count')
        if any(type(usage.get(key)) is not int or usage[key] < 0 for key in required):
            return None
        return (usage['prompt_token_count'] * self.input_usd_per_million
                + (usage['candidates_token_count'] + usage['thoughts_token_count'])
                * self.output_usd_per_million) / 1_000_000


def billed(reservation):
    # Reservations stored before the flag existed were billed only on Vertex.
    return reservation.get('billed', reservation.get('provider') == 'vertex')


def planner_manifest():
    provider = configured_provider()
    return {'provider': provider, 'model': configured_model(provider),
            'project': os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('VERTEX_PROJECT') or os.getenv('GCP_PROJECT'),
            'location': os.getenv('GOOGLE_CLOUD_LOCATION') or os.getenv('VERTEX_LOCATION', 'global'),
            'endpoint_hash': manifest_endpoint_hash(provider, planner_host()),
            'limits_and_rates': asdict(ResearchModelPolicy.from_env())}


def thesis_writer_manifest():
    provider = thesis_provider()
    return {'provider': provider, 'model': configured_model(provider, override='RESEARCH_THESIS_MODEL'),
            'project': os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('VERTEX_PROJECT') or os.getenv('GCP_PROJECT'),
            'location': os.getenv('GOOGLE_CLOUD_LOCATION') or os.getenv('VERTEX_LOCATION', 'global'),
            'endpoint_hash': manifest_endpoint_hash(provider, thesis_host()),
            'limits_and_rates': asdict(ResearchModelPolicy.from_env(THESIS_PREFIX))}
