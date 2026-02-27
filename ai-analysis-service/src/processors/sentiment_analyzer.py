from __future__ import annotations

import os

from transformers import pipeline


class SentimentAnalyzer:
    """DistilBERT sentiment analyzer with batch scoring support."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.getenv(
            "SENTIMENT_MODEL", "distilbert-base-uncased-finetuned-sst-2-english"
        )
        self.batch_size = int(os.getenv("SENTIMENT_BATCH_SIZE", "16"))
        self._pipe = pipeline("sentiment-analysis", model=self.model_name)

    @staticmethod
    def _to_signed_score(result: dict) -> tuple[float, float]:
        label = str(result.get("label", "NEUTRAL")).upper()
        confidence = float(result.get("score", 0.0))
        signed = confidence if "POS" in label else -confidence if "NEG" in label else 0.0
        return signed, confidence

    def score(self, text: str) -> tuple[float, float]:
        result = self._pipe(text[:512])[0]
        return self._to_signed_score(result)

    def score_batch(self, texts: list[str]) -> list[tuple[float, float]]:
        if not texts:
            return []

        bounded = [text[:512] for text in texts]
        results = self._pipe(bounded, batch_size=self.batch_size)
        return [self._to_signed_score(item) for item in results]
