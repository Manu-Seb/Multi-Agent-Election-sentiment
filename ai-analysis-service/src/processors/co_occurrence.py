from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

from entity_graph.models import CoOccurrencePair, EntityMention


def detect_co_occurrence_pairs(
    entities: list[EntityMention],
    sentence_indices: list[int],
    adjacency_window: int = 1,
) -> list[CoOccurrencePair]:
    """Detect co-occurrence in same sentence and adjacent sentence windows."""
    if not entities:
        return []
    if len(entities) != len(sentence_indices):
        raise ValueError("entities and sentence_indices must have equal length")

    sentence_to_entities: dict[int, set[str]] = defaultdict(set)
    for mention, sentence_index in zip(entities, sentence_indices):
        text = mention.text.strip()
        if text:
            sentence_to_entities[sentence_index].add(text)

    counts: Counter[tuple[str, str]] = Counter()
    sorted_indices = sorted(sentence_to_entities.keys())

    for sentence_index in sorted_indices:
        current_entities = sorted(sentence_to_entities[sentence_index])
        for left, right in combinations(current_entities, 2):
            counts[(left, right)] += 1

        for delta in range(1, adjacency_window + 1):
            adjacent_index = sentence_index + delta
            if adjacent_index not in sentence_to_entities:
                continue
            adjacent_entities = sorted(sentence_to_entities[adjacent_index])
            for left in current_entities:
                for right in adjacent_entities:
                    if left == right:
                        continue
                    pair = tuple(sorted((left, right)))
                    counts[pair] += 1

    return [
        CoOccurrencePair(entity_1=left, entity_2=right, count=count)
        for (left, right), count in counts.items()
    ]
