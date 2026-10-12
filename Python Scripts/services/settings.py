"""Process settings: the environment is read once, into one typed object.

Entrypoints call ``load_process_settings()`` at process start: it loads .env, then reads
the environment once.
Library code reads ``get_settings()`` when it runs, never at import, so importing a module
does not depend on the environment and tests can inject settings with ``use_settings``.
The pipeline manifest records the loaded object, so it describes the settings a decision
actually used.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Iterator, Mapping

from services.evidence_continuation_policy import EvidenceContinuationPolicy
from services.gemini_models import parse_thinking_level
from services.runtime_paths import ROOT_DIR, load_project_env


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_LLAMACPP_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_STAGE_MODEL = "world-news-sectors"
_MODEL_FALLBACKS = ("MACRO_NEWS_MODEL", "WORLD_NEWS_MODEL")
_HOST_FALLBACKS = ("MACRO_NEWS_OLLAMA_HOST", "WORLD_NEWS_OLLAMA_HOST")


def _first(environ: Mapping[str, str], names: tuple[str, ...], default: str) -> str:
    # Matches nested os.getenv(a, os.getenv(b, default)): a set-but-empty variable wins.
    return next((environ[name] for name in names if name in environ), default)


def _integer(environ, name, default, minimum):
    return max(minimum, int(environ.get(name, str(default))))


def _decimal(environ, name, default, minimum):
    return max(minimum, float(environ.get(name, str(default))))


def _optional_temperature(environ, name, default):
    # Set-but-blank means "no override": each call keeps the temperature its caller asked for.
    raw = environ.get(name, str(default)).strip()
    if not raw:
        return None
    value = float(raw)
    if not 0.0 <= value <= 2.0:
        raise ValueError(f"{name} must be between 0 and 2, or blank")
    return value


@dataclass(frozen=True)
class StageModel:
    model: str
    ollama_host: str
    # Gemini 3+ only. Blank falls back to VERTEX_THINKING_LEVEL, then the model default.
    thinking_level: str = ""
    # Blank uses LLM_PROVIDER.
    provider: str = ""

    @classmethod
    def from_environ(cls, environ, *prefixes: str) -> "StageModel":
        """Resolve ``<PREFIX>_MODEL`` / ``<PREFIX>_OLLAMA_HOST`` through each prefix, then macro/world news.

        ``<PREFIX>_THINKING_LEVEL`` and ``<PREFIX>_PROVIDER`` do not fall back to the news stages:
        a manager inheriting the news classifiers' low thinking or free-tier provider would
        silently weaken the final decision.
        """
        own_prefixes = prefixes or ("MACRO_NEWS", "WORLD_NEWS")
        thinking_names = tuple(p + "_THINKING_LEVEL" for p in own_prefixes)
        return cls(model=_first(environ, (*(p + "_MODEL" for p in prefixes), *_MODEL_FALLBACKS), DEFAULT_STAGE_MODEL),
                   ollama_host=_first(environ, (*(p + "_OLLAMA_HOST" for p in prefixes), *_HOST_FALLBACKS),
                                      DEFAULT_OLLAMA_HOST),
                   thinking_level=parse_thinking_level(_first(environ, thinking_names, ""), thinking_names[0]),
                   provider=_first(environ, tuple(p + "_PROVIDER" for p in own_prefixes), "").strip().lower())


@dataclass(frozen=True)
class ModelSettings:
    macro_news: StageModel
    sector_opportunist: StageModel
    industry_opportunist: StageModel
    company_opportunist: StageModel
    strategist: StageModel
    manager: StageModel
    thesis_validator: StageModel

    @classmethod
    def from_environ(cls, environ) -> "ModelSettings":
        # The historical environment names keep their spelling.
        return cls(macro_news=StageModel.from_environ(environ),
                   sector_opportunist=StageModel.from_environ(environ, "SECTOR_OPPURUNTIST"),
                   industry_opportunist=StageModel.from_environ(environ, "INDUSTRY_OPPURUNTINST"),
                   company_opportunist=StageModel.from_environ(environ, "COMPANY_OPPURUNTIST"),
                   strategist=StageModel.from_environ(environ, "STRATEGIST"),
                   manager=StageModel.from_environ(environ, "MANAGER"),
                   thesis_validator=StageModel.from_environ(environ, "THESIS_VALIDATOR", "MANAGER"))


@dataclass(frozen=True)
class VertexSettings:
    max_attempts: int
    retry_initial_delay_seconds: float
    retry_max_delay_seconds: float
    explicit_cache: bool
    cache_ttl_seconds: int
    # Gemini 3+ only; see services/gemini_models.py.
    thinking_level: str = ""
    gemini3_temperature: float | None = 1.0

    @classmethod
    def from_environ(cls, environ) -> "VertexSettings":
        initial = _decimal(environ, "VERTEX_RETRY_INITIAL_DELAY_SECONDS", 2, 0.5)
        return cls(max_attempts=_integer(environ, "VERTEX_MAX_ATTEMPTS", 5, 1),
                   retry_initial_delay_seconds=initial,
                   retry_max_delay_seconds=_decimal(environ, "VERTEX_RETRY_MAX_DELAY_SECONDS", 30, initial),
                   explicit_cache=environ.get("VERTEX_EXPLICIT_CACHE", "").strip().lower() in {"1", "true", "yes"},
                   cache_ttl_seconds=_integer(environ, "VERTEX_CACHE_TTL_SECONDS", 900, 60),
                   thinking_level=parse_thinking_level(environ.get("VERTEX_THINKING_LEVEL"), "VERTEX_THINKING_LEVEL"),
                   gemini3_temperature=_optional_temperature(environ, "VERTEX_GEMINI3_TEMPERATURE", 1.0))


@dataclass(frozen=True)
class LlamaCppSettings:
    # llama-server's OpenAI-compatible root. One server hosts one model, so the context
    # size (-c) and GPU offload are server flags, not request settings.
    base_url: str = DEFAULT_LLAMACPP_BASE_URL

    @classmethod
    def from_environ(cls, environ) -> "LlamaCppSettings":
        return cls(base_url=environ.get("LLAMACPP_BASE_URL", "").strip() or DEFAULT_LLAMACPP_BASE_URL)


@dataclass(frozen=True)
class OpenAICompatibleSettings:
    # Any hosted Chat Completions API (Mistral, Groq, OpenRouter, Cerebras). The key is read from
    # OPENAI_COMPATIBLE_API_KEY when the client is built, so it never reaches the pipeline manifest.
    base_url: str = ""

    @classmethod
    def from_environ(cls, environ) -> "OpenAICompatibleSettings":
        return cls(base_url=environ.get("OPENAI_COMPATIBLE_BASE_URL", "").strip())


@dataclass(frozen=True)
class FallbackModel:
    provider: str
    model: str


@dataclass(frozen=True)
class ClassifierFallbackSettings:
    """Models that answer a news classification call, in order, once the one before refuses it for quota.

    CLASSIFIER_FALLBACKS is a comma-separated list of provider:model; empty disables the fallback.
    """
    chain: tuple[FallbackModel, ...] = ()

    @classmethod
    def from_environ(cls, environ) -> "ClassifierFallbackSettings":
        chain = []
        for entry in filter(None, (item.strip() for item in environ.get("CLASSIFIER_FALLBACKS", "").split(","))):
            provider, _, model = (part.strip() for part in entry.partition(":"))
            if not provider or not model:
                raise ValueError(f"CLASSIFIER_FALLBACKS entries must be provider:model, got '{entry}'")
            chain.append(FallbackModel(provider=provider.lower(), model=model))
        return cls(chain=tuple(chain))


@dataclass(frozen=True)
class ManagerContextSettings:
    history_limit: int
    history_max_age_days: int
    history_article_limit: int
    resolved_autopsy_limit: int
    resolved_autopsy_max_age_days: int
    option_chain_limit_per_type: int
    option_chain_fetch_multiplier: int
    option_fetch_min: int
    underlying_history_lookback_days: int
    hv_short_window_days: int
    hv_long_window_days: int
    iv_history_path: Path
    iv_history_max_entries: int
    iv_bucket_min_history_samples: int

    @classmethod
    def from_environ(cls, environ) -> "ManagerContextSettings":
        short_window = _integer(environ, "MANAGER_UNDERLYING_HV_SHORT_WINDOW_DAYS", 20, 20)
        return cls(
            history_limit=_integer(environ, "MANAGER_DECISION_HISTORY_LIMIT", 3, 1),
            history_max_age_days=_integer(environ, "MANAGER_DECISION_HISTORY_MAX_AGE_DAYS", 30, 1),
            history_article_limit=_integer(environ, "MANAGER_DECISION_HISTORY_ARTICLE_LIMIT", 3, 1),
            resolved_autopsy_limit=_integer(environ, "MANAGER_RESOLVED_AUTOPSY_LIMIT", 3, 1),
            resolved_autopsy_max_age_days=_integer(environ, "MANAGER_RESOLVED_AUTOPSY_MAX_AGE_DAYS", 180, 1),
            option_chain_limit_per_type=_integer(environ, "MANAGER_OPTION_CHAIN_LIMIT_PER_TYPE", 6, 1),
            option_chain_fetch_multiplier=_integer(environ, "MANAGER_OPTION_CHAIN_FETCH_MULTIPLIER", 6, 2),
            option_fetch_min=_integer(environ, "MANAGER_OPTION_FETCH_MIN", 500, 100),
            underlying_history_lookback_days=_integer(environ, "MANAGER_UNDERLYING_HISTORY_LOOKBACK_DAYS", 120, 90),
            hv_short_window_days=short_window,
            hv_long_window_days=_integer(environ, "MANAGER_UNDERLYING_HV_LONG_WINDOW_DAYS", 60, short_window),
            iv_history_path=Path(environ.get("OPTION_IV_HISTORY_PATH",
                                             str(ROOT_DIR / "shared" / "option_iv_history.json"))),
            iv_history_max_entries=_integer(environ, "OPTION_IV_HISTORY_MAX_ENTRIES", 252, 30),
            iv_bucket_min_history_samples=_integer(environ, "OPTION_IV_BUCKET_MIN_HISTORY_SAMPLES", 20, 1),
        )


@dataclass(frozen=True)
class OpportunistSettings:
    company_top_companies_limit: int
    company_ranked_companies_limit: int
    company_article_body_char_limit: int
    macro_news_batch_pause_seconds: float

    @classmethod
    def from_environ(cls, environ) -> "OpportunistSettings":
        return cls(
            company_top_companies_limit=_integer(environ, "COMPANY_OPPORTUNIST_TOP_COMPANIES_LIMIT", 12, 1),
            company_ranked_companies_limit=_integer(environ, "COMPANY_OPPORTUNIST_RANKED_COMPANIES_LIMIT", 6, 1),
            company_article_body_char_limit=_integer(environ, "COMPANY_OPPORTUNIST_ARTICLE_BODY_CHAR_LIMIT", 1200, 200),
            macro_news_batch_pause_seconds=_decimal(environ, "MACRO_NEWS_BATCH_PAUSE_SECONDS", 0.75, 0.0),
        )


@dataclass(frozen=True)
class Settings:
    models: ModelSettings
    vertex: VertexSettings
    llamacpp: LlamaCppSettings
    manager_context: ManagerContextSettings
    opportunists: OpportunistSettings
    evidence_continuation: EvidenceContinuationPolicy
    openai_compatible: OpenAICompatibleSettings = OpenAICompatibleSettings()
    classifier_fallback: ClassifierFallbackSettings = ClassifierFallbackSettings()

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "Settings":
        return cls(models=ModelSettings.from_environ(environ), vertex=VertexSettings.from_environ(environ),
                   llamacpp=LlamaCppSettings.from_environ(environ),
                   manager_context=ManagerContextSettings.from_environ(environ),
                   opportunists=OpportunistSettings.from_environ(environ),
                   evidence_continuation=EvidenceContinuationPolicy.from_environ(environ),
                   openai_compatible=OpenAICompatibleSettings.from_environ(environ),
                   classifier_fallback=ClassifierFallbackSettings.from_environ(environ))


_settings: Settings | None = None


def load_settings() -> Settings:
    """Read the process environment once. Entrypoints call this after loading .env."""
    global _settings
    _settings = Settings.from_environ(os.environ)
    return _settings


def load_process_settings() -> Settings:
    """Entrypoint start: load the checkout's .env, then read the environment once."""
    load_project_env()
    return load_settings()


def get_settings() -> Settings:
    return _settings if _settings is not None else load_settings()


@contextmanager
def use_settings(settings: Settings | None = None, **sections) -> Iterator[Settings]:
    """Temporarily run with explicit settings, or with some sections replaced (tests)."""
    global _settings
    previous = _settings
    _settings = replace(settings or get_settings(), **sections)
    try:
        yield _settings
    finally:
        _settings = previous
