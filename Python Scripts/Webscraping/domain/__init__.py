"""Typed data contracts shared by scraping discovery, extraction, and storage."""

from .models import CandidateArticle, ExtractedArticle, FetchResult, ScrapeSummary

__all__ = ["CandidateArticle", "ExtractedArticle", "FetchResult", "ScrapeSummary"]
