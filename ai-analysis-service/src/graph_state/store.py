from __future__ import annotations

import asyncio
import copy
import os
import resource
import time
import tracemalloc
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from entity_graph.models import CoOccurrencePair, EntityMention, EntitySentimentResult

from .models import Edge, GraphState, Node, serialize_edge, serialize_node


class InMemoryGraphStore:
    """Thread-safe in-memory graph with O(1) node/edge lookups and change events."""

    NODE_ALPHA = 0.05
    EDGE_ALPHA = 0.05

    def __init__(self) -> None:
        self._state = GraphState(nodes={}, edges={})
        self._lock = asyncio.Lock()
        self._mention_windows: dict[str, deque[datetime]] = defaultdict(deque)
        self._neighbors: dict[str, set[str]] = defaultdict(set)

        self._sentiment_delta_threshold = float(os.getenv("GRAPH_CHANGE_SENTIMENT_THRESHOLD", "0.01"))
        self._centrality_delta_threshold = float(os.getenv("GRAPH_CHANGE_CENTRALITY_THRESHOLD", "0.0001"))
        self._strength_delta_threshold = float(os.getenv("GRAPH_CHANGE_STRENGTH_THRESHOLD", "0.0001"))
        self._joint_sent_delta_threshold = float(os.getenv("GRAPH_CHANGE_JOINT_SENTIMENT_THRESHOLD", "0.01"))

        tracemalloc.start()

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(tz=timezone.utc)

    @staticmethod
    def _edge_key(left: str, right: str) -> tuple[str, str]:
        return (left, right) if left <= right else (right, left)

    @staticmethod
    def _weighted(old: float, new: float, alpha: float) -> float:
        return (old * (1.0 - alpha)) + (new * alpha)

    @staticmethod
    def _round4(value: float) -> float:
        return round(float(value), 6)

    def _update_hourly_rate(self, entity_id: str, now: datetime) -> float:
        window = self._mention_windows[entity_id]
        window.append(now)
        lower_bound = now - timedelta(hours=1)
        while window and window[0] < lower_bound:
            window.popleft()
        return float(len(window))

    def _update_centrality(self, impacted: set[str]) -> None:
        total_nodes = len(self._state.nodes)
        denom = max(1, total_nodes - 1)
        for entity_id in impacted:
            node = self._state.nodes.get(entity_id)
            if node is None:
                continue
            degree = len(self._neighbors.get(entity_id, set()))
            node.centrality = float(degree) / float(denom)

    def _upsert_node(self, mention: EntityMention, now: datetime, touched: set[str]) -> None:
        entity_id = mention.text.strip()
        if not entity_id:
            return

        touched.add(entity_id)

        if entity_id not in self._state.nodes:
            hourly_rate = self._update_hourly_rate(entity_id, now)
            self._state.nodes[entity_id] = Node(
                id=entity_id,
                type=mention.type,
                mention_count=1,
                sentiment=mention.sentiment_score,
                centrality=0.0,
                first_seen=now,
                last_seen=now,
                hourly_rate=hourly_rate,
            )
            return

        node = self._state.nodes[entity_id]
        node.mention_count += 1
        node.sentiment = self._weighted(node.sentiment, mention.sentiment_score, self.NODE_ALPHA)
        node.last_seen = now
        node.type = mention.type or node.type
        node.hourly_rate = self._update_hourly_rate(entity_id, now)

    def _upsert_edge(self, pair: CoOccurrencePair, now: datetime, touched: set[str]) -> None:
        source = pair.entity_1.strip()
        target = pair.entity_2.strip()
        if not source or not target or source == target:
            return

        key = self._edge_key(source, target)

        source_node = self._state.nodes.get(source)
        target_node = self._state.nodes.get(target)
        if source_node is None or target_node is None:
            return

        joint = (source_node.sentiment + target_node.sentiment) / 2.0

        if key not in self._state.edges:
            self._state.edges[key] = Edge(
                source=key[0],
                target=key[1],
                strength=float(pair.count),
                joint_sentiment=joint,
                first_seen=now,
                last_strengthened=now,
            )
        else:
            edge = self._state.edges[key]
            edge.strength = self._weighted(edge.strength, float(pair.count), self.EDGE_ALPHA)
            edge.joint_sentiment = self._weighted(edge.joint_sentiment, joint, self.EDGE_ALPHA)
            edge.last_strengthened = now

        self._neighbors[key[0]].add(key[1])
        self._neighbors[key[1]].add(key[0])
        touched.add(key[0])
        touched.add(key[1])

    @staticmethod
    def _collect_affected_keys(result: EntitySentimentResult) -> tuple[set[str], set[tuple[str, str]]]:
        node_keys = {mention.text.strip() for mention in result.entities if mention.text.strip()}
        edge_keys = {
            (pair.entity_1.strip(), pair.entity_2.strip())
            for pair in result.co_occurrence_pairs
            if pair.entity_1.strip() and pair.entity_2.strip() and pair.entity_1.strip() != pair.entity_2.strip()
        }
        norm_edge_keys = {(a, b) if a <= b else (b, a) for (a, b) in edge_keys}
        return node_keys, norm_edge_keys

    @staticmethod
    def _snapshot_nodes(state: GraphState, node_keys: set[str]) -> dict[str, Node | None]:
        return {key: copy.deepcopy(state.nodes.get(key)) for key in node_keys}

    @staticmethod
    def _snapshot_edges(state: GraphState, edge_keys: set[tuple[str, str]]) -> dict[tuple[str, str], Edge | None]:
        return {key: copy.deepcopy(state.edges.get(key)) for key in edge_keys}

    def _build_entity_changes(
        self,
        before: dict[str, Node | None],
        after: dict[str, Node | None],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for node_id in sorted(set(before.keys()) | set(after.keys())):
            old_node = before.get(node_id)
            new_node = after.get(node_id)
            if new_node is None:
                continue

            old_mentions = old_node.mention_count if old_node else 0
            new_mentions = new_node.mention_count
            delta_mentions = new_mentions - old_mentions

            old_sentiment = old_node.sentiment if old_node else 0.0
            new_sentiment = new_node.sentiment
            delta_sentiment = new_sentiment - old_sentiment

            old_centrality = old_node.centrality if old_node else 0.0
            new_centrality = new_node.centrality
            delta_centrality = new_centrality - old_centrality

            changed = (
                delta_mentions != 0
                or abs(delta_sentiment) >= self._sentiment_delta_threshold
                or abs(delta_centrality) >= self._centrality_delta_threshold
            )
            if not changed:
                continue

            changes.append(
                {
                    "id": node_id,
                    "old_mentions": old_mentions,
                    "new_mentions": new_mentions,
                    "delta_mentions": delta_mentions,
                    "old_sentiment": self._round4(old_sentiment),
                    "new_sentiment": self._round4(new_sentiment),
                    "delta_sentiment": self._round4(delta_sentiment),
                    "old_centrality": self._round4(old_centrality),
                    "new_centrality": self._round4(new_centrality),
                    "delta_centrality": self._round4(delta_centrality),
                }
            )
        return changes

    def _build_relationship_changes(
        self,
        before: dict[tuple[str, str], Edge | None],
        after: dict[tuple[str, str], Edge | None],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            old_edge = before.get(key)
            new_edge = after.get(key)
            if new_edge is None:
                continue

            old_strength = old_edge.strength if old_edge else 0.0
            new_strength = new_edge.strength
            delta_strength = new_strength - old_strength

            old_joint = old_edge.joint_sentiment if old_edge else 0.0
            new_joint = new_edge.joint_sentiment
            delta_joint = new_joint - old_joint

            changed = (
                abs(delta_strength) >= self._strength_delta_threshold
                or abs(delta_joint) >= self._joint_sent_delta_threshold
            )
            if not changed:
                continue

            changes.append(
                {
                    "source": key[0],
                    "target": key[1],
                    "old_strength": self._round4(old_strength),
                    "new_strength": self._round4(new_strength),
                    "delta_strength": self._round4(delta_strength),
                    "old_joint_sentiment": self._round4(old_joint),
                    "new_joint_sentiment": self._round4(new_joint),
                    "delta_joint_sentiment": self._round4(delta_joint),
                }
            )
        return changes

    def _validate_changes_apply(
        self,
        before_nodes: dict[str, Node | None],
        before_edges: dict[tuple[str, str], Edge | None],
        after_nodes: dict[str, Node | None],
        after_edges: dict[tuple[str, str], Edge | None],
        entity_changes: list[dict[str, Any]],
        relationship_changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        errors: list[str] = []

        for item in entity_changes:
            node_id = item["id"]
            expected_mentions = item["old_mentions"] + item["delta_mentions"]
            expected_sent = item["old_sentiment"] + item["delta_sentiment"]
            expected_cent = item["old_centrality"] + item["delta_centrality"]

            after_node = after_nodes.get(node_id)
            if after_node is None:
                errors.append(f"missing after node {node_id}")
                continue

            if expected_mentions != after_node.mention_count:
                errors.append(f"mention mismatch for {node_id}")
            if abs(expected_sent - after_node.sentiment) > 0.02:
                errors.append(f"sentiment mismatch for {node_id}")
            if abs(expected_cent - after_node.centrality) > 0.02:
                errors.append(f"centrality mismatch for {node_id}")

        for item in relationship_changes:
            key = self._edge_key(item["source"], item["target"])
            expected_strength = item["old_strength"] + item["delta_strength"]
            expected_joint = item["old_joint_sentiment"] + item["delta_joint_sentiment"]

            after_edge = after_edges.get(key)
            if after_edge is None:
                errors.append(f"missing after edge {key[0]}::{key[1]}")
                continue

            if abs(expected_strength - after_edge.strength) > 0.02:
                errors.append(f"strength mismatch for {key[0]}::{key[1]}")
            if abs(expected_joint - after_edge.joint_sentiment) > 0.02:
                errors.append(f"joint_sentiment mismatch for {key[0]}::{key[1]}")

        return {"passed": len(errors) == 0, "errors": errors}

    async def apply_result(
        self,
        result: EntitySentimentResult,
        observed_at: datetime | None = None,
        extraction_ms: int = 0,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        now = observed_at or self._now_utc()
        observed_at_ms = int(now.timestamp() * 1000)

        node_keys, edge_keys = self._collect_affected_keys(result)
        graph_update_start = time.perf_counter()

        async with self._lock:
            before_nodes = self._snapshot_nodes(self._state, node_keys)
            before_edges = self._snapshot_edges(self._state, edge_keys)

            touched: set[str] = set()
            for mention in result.entities:
                self._upsert_node(mention, now, touched)

            for pair in result.co_occurrence_pairs:
                self._upsert_edge(pair, now, touched)

            self._update_centrality(touched)

            after_nodes = self._snapshot_nodes(self._state, node_keys)
            after_edges = self._snapshot_edges(self._state, edge_keys)

            entity_changes = self._build_entity_changes(before_nodes, after_nodes)
            relationship_changes = self._build_relationship_changes(before_edges, after_edges)

            validation = self._validate_changes_apply(
                before_nodes=before_nodes,
                before_edges=before_edges,
                after_nodes=after_nodes,
                after_edges=after_edges,
                entity_changes=entity_changes,
                relationship_changes=relationship_changes,
            )

            graph_update_ms = int((time.perf_counter() - graph_update_start) * 1000)

            return {
                "schema_version": schema_version,
                "timestamp": observed_at_ms,
                "article_id": result.article_id,
                "entity_changes": entity_changes,
                "relationship_changes": relationship_changes,
                "processing_metrics": {
                    "extraction_ms": int(extraction_ms),
                    "graph_update_ms": graph_update_ms,
                },
                "validation": validation,
                "node_count": len(self._state.nodes),
                "edge_count": len(self._state.edges),
            }

    async def get_current_state(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "node_count": len(self._state.nodes),
                "edge_count": len(self._state.edges),
                "nodes": [serialize_node(node) for node in self._state.nodes.values()],
                "edges": [serialize_edge(edge) for edge in self._state.edges.values()],
            }

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        async with self._lock:
            node = self._state.nodes.get(entity_id)
            return serialize_node(node) if node else None

    async def get_edges_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            edges = []
            for edge in self._state.edges.values():
                if edge.source == entity_id or edge.target == entity_id:
                    edges.append(serialize_edge(edge))
            return edges

    async def get_metrics(self) -> dict[str, Any]:
        async with self._lock:
            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
            return {
                "node_count": len(self._state.nodes),
                "edge_count": len(self._state.edges),
                "memory_current_bytes": current_bytes,
                "memory_peak_bytes": peak_bytes,
                "memory_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
