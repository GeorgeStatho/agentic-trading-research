"""Gemini model-generation rules shared by every Vertex call path.

Gemini 2.5 retires on Vertex AI on 2026-10-20, and Gemini 3+ changed the request contract:
thinking is a named level rather than a token budget, and Google recommends the default
temperature of 1.0 (lower values can loop or degrade reasoning).
"""
from __future__ import annotations

import os
import re


DEFAULT_VERTEX_MODEL = "gemini-3.8-flash"
THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})
# Gemini 3.8 rejects MINIMAL with 400 INVALID_ARGUMENT; LOW is its lightest supported level.
_FIRST_VERSION_WITHOUT_MINIMAL = (3, 8)
_VERSION_PATTERN = re.compile(r"gemini-(\d+)(?:\.(\d+))?")


def default_vertex_model() -> str:
    return str(os.getenv("VERTEX_DEFAULT_MODEL") or DEFAULT_VERTEX_MODEL).strip()


def gemini_version(model: str | None) -> tuple[int, int] | None:
    match = _VERSION_PATTERN.search(str(model or "").strip().lower())
    return (int(match[1]), int(match[2] or 0)) if match else None


def uses_thinking_levels(model: str | None) -> bool:
    """Gemini 3+ takes ``thinking_level``; Gemini 2.5 takes ``thinking_budget`` instead."""
    version = gemini_version(model)
    return version is not None and version >= (3, 0)


def parse_thinking_level(value: str | None, name: str) -> str:
    level = str(value or "").strip().lower()
    if level and level not in THINKING_LEVELS:
        raise ValueError(f"{name} must be blank or one of {', '.join(sorted(THINKING_LEVELS))}")
    return level


def thinking_level_for(model: str | None, level: str | None) -> str:
    """The level to send ``model``; blank keeps the model default."""
    level = str(level or "").strip().lower()
    version = gemini_version(model)
    if level == "minimal" and version is not None and version >= _FIRST_VERSION_WITHOUT_MINIMAL:
        return "low"
    return level


def temperature_for(model: str | None, requested: float, gemini3_temperature: float | None) -> float:
    """Gemini 3+ runs at ``gemini3_temperature`` unless it is None; older models keep ``requested``."""
    if gemini3_temperature is not None and uses_thinking_levels(model):
        return gemini3_temperature
    return requested
