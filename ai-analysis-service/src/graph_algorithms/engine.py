from __future__ import annotations

import json
import math
import random
from collections import defaultdict, deque
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from entity_graph.models import EntitySentimentResult

from .config import AlgorithmConfig
from .models import AlgoEdge, AlgoNode, MetricsSnapshot


class GraphAlgorithmsEngine:
    """Maintains algorithmic graph state and computes incremental metrics.

    Math notes:
    - Relationship strength update uses multiplicative factors:
      delta = proximity * recency * alignment.
    - Recency decay uses exponential kernel exp(-hours_since / halflife).
    - Alignment penalizes sentiment disagreement: 1 - |s1 - s2|/2 in [0,1].
    - PageRank uses power iteration with damping on affected subgraph.
    """

    def __init__(self, config: AlgorithmConfig) -> None:
        self.config = config
        self.nodes: dict[str, AlgoNode] = {}
        self.edges: dict[tuple[str, str], AlgoEdge] = {}
        self.neighbors: dict[str, set[str]] = defaultdict(set)
        self.updates_processed = 0
        self._last_communities: dict[str, int] = {}
        self._rng = random.Random(42)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=timezone.utc)

    @staticmethod
    def _edge_key(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    @staticmethod
    def _avg(a: float, b: float) -> float:
        return (a + b) / 2.0

    def _hours_since(self, then: datetime, now: datetime) -> float:
        return max(0.0, (now - then).total_seconds() / 3600.0)

    def _recency_factor(self, hours_since: float) -> float:
        # exp(-hours/halflife) gives smooth time decay favoring recent events.
        return math.exp(-hours_since / max(1e-6, self.config.recency_halflife_hours))

    def _alignment(self, s1: float, s2: float) -> float:
        # sentiments are in [-1,1], so |s1-s2| in [0,2]; normalize to [0,1].
        return max(0.0, 1.0 - abs(s1 - s2) / 2.0)

    def _proximity_score(self) -> float:
        mapping = {
            "same_sentence": self.config.proximity_same_sentence,
            "adjacent": self.config.proximity_adjacent_sentence,
            "same_paragraph": self.config.proximity_same_paragraph,
        }
        return mapping.get(self.config.proximity_default_class, self.config.proximity_same_sentence)

    def _relationship_delta(self, s1: float, s2: float, last_seen: datetime | None, now: datetime) -> float:
        proximity = self._proximity_score()
        hours = self._hours_since(last_seen, now) if last_seen else 0.0
        recency = self._recency_factor(hours)
        alignment = self._alignment(s1, s2)
        return proximity * recency * alignment

    def _upsert_node(self, entity_text: str, entity_type: str, sentiment: float, now: datetime) -> None:
        if entity_text not in self.nodes:
            self.nodes[entity_text] = AlgoNode(
                node_id=entity_text,
                node_type=entity_type,
                sentiment=sentiment,
                mention_count=1,
                first_seen=now,
                last_seen=now,
            )
            return

        node = self.nodes[entity_text]
        node.sentiment = node.sentiment * 0.95 + sentiment * 0.05
        node.mention_count += 1
        node.last_seen = now
        if entity_type:
            node.node_type = entity_type

    def apply_result(self, result: EntitySentimentResult, processed_at_ms: int) -> MetricsSnapshot:
        now = datetime.fromtimestamp(processed_at_ms / 1000.0, tz=timezone.utc)
        touched: set[str] = set()

        for mention in result.entities:
            text = mention.text.strip()
            if not text:
                continue
            self._upsert_node(text, mention.type, mention.sentiment_score, now)
            touched.add(text)

        for pair in result.co_occurrence_pairs:
            a = pair.entity_1.strip()
            b = pair.entity_2.strip()
            if not a or not b or a == b or a not in self.nodes or b not in self.nodes:
                continue

            key = self._edge_key(a, b)
            node_a, node_b = self.nodes[a], self.nodes[b]
            joint = self._avg(node_a.sentiment, node_b.sentiment)
            edge = self.edges.get(key)
            delta = self._relationship_delta(node_a.sentiment, node_b.sentiment, edge.last_strengthened if edge else None, now)

            if edge is None:
                self.edges[key] = AlgoEdge(
                    source=key[0],
                    target=key[1],
                    strength=delta,
                    joint_sentiment=joint,
                    first_seen=now,
                    last_strengthened=now,
                )
            else:
                edge.strength += delta
                edge.joint_sentiment = edge.joint_sentiment * 0.95 + joint * 0.05
                edge.last_strengthened = now

            self.neighbors[key[0]].add(key[1])
            self.neighbors[key[1]].add(key[0])
            touched.add(key[0])
            touched.add(key[1])

        self._update_pagerank_incremental(touched)
        if self.config.betweenness_enabled:
            self._update_betweenness_approx(touched)

        community_changes: list[dict[str, int]] = []
        self.updates_processed += 1
        if self.updates_processed % max(1, self.config.community_interval) == 0:
            community_changes = self._run_leiden_like()

        if self.config.visualization_enabled and self.updates_processed % max(1, self.config.visualization_interval) == 0:
            self._write_debug_visuals(processed_at_ms)

        return MetricsSnapshot(
            timestamp_ms=processed_at_ms,
            updates_processed=self.updates_processed,
            node_count=len(self.nodes),
            edge_count=len(self.edges),
            touched_nodes=sorted(touched),
            community_changes=community_changes,
        )

    def _affected_subgraph(self, touched: set[str]) -> set[str]:
        if not touched:
            return set()
        hops = max(0, self.config.pagerank_hops)
        seen = set(touched)
        frontier = deque((node, 0) for node in touched)
        while frontier:
            node, depth = frontier.popleft()
            if depth >= hops:
                continue
            for nei in self.neighbors.get(node, set()):
                if nei in seen:
                    continue
                seen.add(nei)
                frontier.append((nei, depth + 1))
        return seen

    def _update_pagerank_incremental(self, touched: set[str]) -> None:
        nodes = self._affected_subgraph(touched)
        if not nodes:
            return

        d = self.config.pagerank_damping
        n = len(nodes)
        base = (1.0 - d) / max(1, n)
        pr = {node: (self.nodes[node].pagerank if self.nodes[node].pagerank > 0 else 1.0 / n) for node in nodes}

        for _ in range(max(1, self.config.pagerank_iterations)):
            nxt = {node: base for node in nodes}
            for node in nodes:
                outgoing = [x for x in self.neighbors.get(node, set()) if x in nodes]
                if not outgoing:
                    continue
                share = d * pr[node] / len(outgoing)
                for nei in outgoing:
                    nxt[nei] += share
            pr = nxt

        norm = sum(pr.values()) or 1.0
        for node, val in pr.items():
            self.nodes[node].pagerank = val / norm

    def _single_source_shortest_paths(self, source: str, subset: set[str]) -> dict[str, int]:
        dist = {source: 0}
        q = deque([source])
        while q:
            cur = q.popleft()
            for nei in self.neighbors.get(cur, set()):
                if nei not in subset or nei in dist:
                    continue
                dist[nei] = dist[cur] + 1
                q.append(nei)
        return dist

    def _update_betweenness_approx(self, touched: set[str]) -> None:
        subset = self._affected_subgraph(touched)
        if len(subset) < 3:
            return

        sample_size = min(len(subset), max(1, self.config.betweenness_samples))
        sources = self._rng.sample(list(subset), k=sample_size)
        score = defaultdict(float)

        for s in sources:
            dist = self._single_source_shortest_paths(s, subset)
            for node, d in dist.items():
                if node == s:
                    continue
                # Approximation proxy: nodes reached by longer shortest paths have higher bridge tendency.
                score[node] += d

        if not score:
            return

        max_score = max(score.values()) or 1.0
        for node in subset:
            self.nodes[node].betweenness = score.get(node, 0.0) / max_score

    def _modularity_gain(self, node: str, target_comm: int, communities: dict[str, int]) -> float:
        gain = 0.0
        for nei in self.neighbors.get(node, set()):
            if communities.get(nei) == target_comm:
                edge = self.edges.get(self._edge_key(node, nei))
                gain += edge.strength if edge else 0.0
        return gain

    def _run_leiden_like(self) -> list[dict[str, int]]:
        # Lightweight Leiden-like local moving phase (modularity-inspired).
        communities = {node_id: idx for idx, node_id in enumerate(self.nodes.keys())}

        improved = True
        while improved:
            improved = False
            for node in self.nodes.keys():
                current = communities[node]
                best_comm = current
                best_gain = 0.0
                neighbor_comms = {communities.get(nei, current) for nei in self.neighbors.get(node, set())}
                for comm in neighbor_comms:
                    gain = self._modularity_gain(node, comm, communities) - self._modularity_gain(node, current, communities)
                    if gain > best_gain:
                        best_gain = gain
                        best_comm = comm
                if best_comm != current:
                    communities[node] = best_comm
                    improved = True

        # Compress ids to dense integers.
        remap = {}
        next_id = 0
        for comm in communities.values():
            if comm not in remap:
                remap[comm] = next_id
                next_id += 1

        changes: list[dict[str, int]] = []
        for node_id, comm in communities.items():
            new_comm = remap[comm]
            old_comm = self._last_communities.get(node_id, -1)
            if new_comm != old_comm:
                changes.append({"node": node_id, "old": old_comm, "new": new_comm})
            self.nodes[node_id].community_id = new_comm
            self._last_communities[node_id] = new_comm

        return changes

    def _write_debug_visuals(self, timestamp_ms: int) -> None:
        out = Path(self.config.visualization_dir)
        out.mkdir(parents=True, exist_ok=True)

        payload = {
            "timestamp_ms": timestamp_ms,
            "nodes": [asdict(node) for node in self.nodes.values()],
            "edges": [asdict(edge) for edge in self.edges.values()],
        }
        (out / f"graph_{timestamp_ms}.json").write_text(json.dumps(payload, default=str, indent=2))

        # Simple Graphviz DOT for quick inspection.
        dot_lines = ["graph G {"]
        for node in self.nodes.values():
            dot_lines.append(
                f'  "{node.node_id}" [label="{node.node_id}\\npr={node.pagerank:.3f}\\ncomm={node.community_id}"];'
            )
        for edge in self.edges.values():
            dot_lines.append(
                f'  "{edge.source}" -- "{edge.target}" [label="{edge.strength:.3f}"];'
            )
        dot_lines.append("}")
        (out / f"graph_{timestamp_ms}.dot").write_text("\n".join(dot_lines))

    def build_metrics_payload(self, snapshot: MetricsSnapshot) -> dict:
        top_pagerank = sorted(self.nodes.values(), key=lambda x: x.pagerank, reverse=True)[:10]
        top_bridges = sorted(self.nodes.values(), key=lambda x: x.betweenness, reverse=True)[:10]
        return {
            "timestamp": snapshot.timestamp_ms,
            "updates_processed": snapshot.updates_processed,
            "node_count": snapshot.node_count,
            "edge_count": snapshot.edge_count,
            "touched_nodes": snapshot.touched_nodes,
            "community_changes": snapshot.community_changes,
            "top_pagerank": [
                {"node": n.node_id, "pagerank": n.pagerank, "community": n.community_id}
                for n in top_pagerank
            ],
            "top_bridge_nodes": [
                {"node": n.node_id, "betweenness": n.betweenness, "community": n.community_id}
                for n in top_bridges
            ],
        }
