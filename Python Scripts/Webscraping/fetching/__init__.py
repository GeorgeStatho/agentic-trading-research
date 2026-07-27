"""Resilient network clients used by scraping discovery adapters."""

from .http_client import HttpClient, HttpFetchError

__all__ = ["HttpClient", "HttpFetchError"]
