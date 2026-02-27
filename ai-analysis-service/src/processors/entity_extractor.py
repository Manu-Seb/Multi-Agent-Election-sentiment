from __future__ import annotations

import os
from dataclasses import dataclass

import spacy

from entity_graph.models import EntityMention


@dataclass(slots=True)
class SentenceSpan:
    index: int
    text: str
    start_char: int
    end_char: int


class EntityExtractor:
    """Sentence-aware spaCy NER extractor with chunk overlap support."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.getenv("SPACY_MODEL", "en_core_web_sm")
        self.chunk_sentence_size = int(os.getenv("CHUNK_SENTENCE_SIZE", "8"))
        self.chunk_sentence_overlap = int(os.getenv("CHUNK_SENTENCE_OVERLAP", "2"))

        self.sent_nlp = spacy.blank("en")
        self.sent_nlp.add_pipe("sentencizer")
        self.ner_nlp = spacy.load(self.model_name)

    def _sentence_spans(self, text: str) -> list[SentenceSpan]:
        doc = self.sent_nlp(text)
        spans: list[SentenceSpan] = []
        for idx, sent in enumerate(doc.sents):
            spans.append(
                SentenceSpan(
                    index=idx,
                    text=sent.text,
                    start_char=sent.start_char,
                    end_char=sent.end_char,
                )
            )
        return spans

    def _build_chunks(self, sentence_count: int) -> list[tuple[int, int]]:
        if sentence_count == 0:
            return []
        if sentence_count <= self.chunk_sentence_size:
            return [(0, sentence_count - 1)]

        chunks: list[tuple[int, int]] = []
        step = max(1, self.chunk_sentence_size - self.chunk_sentence_overlap)
        start = 0
        while start < sentence_count:
            end = min(sentence_count - 1, start + self.chunk_sentence_size - 1)
            chunks.append((start, end))
            if end >= sentence_count - 1:
                break
            start += step
        return chunks

    @staticmethod
    def _find_sentence_index(sentence_spans: list[SentenceSpan], char_pos: int) -> int:
        for span in sentence_spans:
            if span.start_char <= char_pos < span.end_char:
                return span.index
        return sentence_spans[-1].index

    def extract(self, text: str) -> tuple[list[EntityMention], list[int], list[str]]:
        sentence_spans = self._sentence_spans(text)
        chunks = self._build_chunks(len(sentence_spans))

        mentions: list[EntityMention] = []
        sentence_indices: list[int] = []
        dedupe: set[tuple[str, str, int, int]] = set()

        for start_idx, end_idx in chunks:
            chunk_start = sentence_spans[start_idx].start_char
            chunk_end = sentence_spans[end_idx].end_char
            chunk_text = text[chunk_start:chunk_end]
            if not chunk_text.strip():
                continue

            chunk_doc = self.ner_nlp(chunk_text)
            for ent in chunk_doc.ents:
                global_start = chunk_start + ent.start_char
                global_end = chunk_start + ent.end_char
                key = (ent.text, ent.label_, global_start, global_end)
                if key in dedupe:
                    continue
                dedupe.add(key)

                sentence_index = self._find_sentence_index(sentence_spans, global_start)
                sentence_text = sentence_spans[sentence_index].text

                mentions.append(
                    EntityMention(
                        text=ent.text,
                        type=ent.label_,
                        sentence=sentence_text,
                        sentiment_score=0.0,
                        confidence=0.0,
                        char_start=global_start,
                        char_end=global_end,
                    )
                )
                sentence_indices.append(sentence_index)

        ordered_sentence_text = [span.text for span in sentence_spans]
        return mentions, sentence_indices, ordered_sentence_text
