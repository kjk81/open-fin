"""Pydantic models for social sentiment analysis tool output."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SentimentSnapshot(BaseModel):
    ticker: str
    overall_bias: str          # "Bullish" | "Bearish" | "Neutral" | "Mixed"
    key_catalysts: list[str]   # Top 3-5 catalysts driving sentiment
    majority_opinion: str      # 2-3 sentence summary of dominant narrative
    reddit_summary: str        # Reddit-specific synthesis
    twitter_summary: str       # Twitter/X-specific synthesis
    confidence: str            # "High" | "Medium" | "Low"
    searched_at: datetime
