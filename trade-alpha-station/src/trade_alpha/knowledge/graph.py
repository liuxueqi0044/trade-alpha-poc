"""Reproducible NetworkX graph snapshots from versioned relationships."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

import networkx as nx  # type: ignore[import-untyped]

from .models import ReviewStatus
from .store import KnowledgeStore


@dataclass(frozen=True)
class GraphSnapshot:
    as_of: datetime
    graph: nx.MultiDiGraph
    digest: str


def build_snapshot(store: KnowledgeStore, as_of: datetime) -> GraphSnapshot:
    """Build a stable directed multigraph, excluding unaccepted relationships."""
    graph = nx.MultiDiGraph()
    for entity in sorted(store.entities_at(as_of), key=lambda item: item.entity_id):
        graph.add_node(
            entity.entity_id, entity_type=entity.entity_type.value, name=entity.canonical_name
        )
    for relationship in sorted(
        store.relationships_at(as_of), key=lambda item: item.relationship_id
    ):
        if relationship.review_status is ReviewStatus.ACCEPTED:
            graph.add_edge(
                relationship.subject_entity_id,
                relationship.object_entity_id,
                key=relationship.relationship_id,
                relationship_type=relationship.relationship_type,
                confidence=float(relationship.confidence),
                source_claim_ids=relationship.source_claim_ids,
            )
    canonical = {
        "nodes": sorted((node, dict(data)) for node, data in graph.nodes(data=True)),
        "edges": sorted(
            (left, right, key, dict(data))
            for left, right, key, data in graph.edges(keys=True, data=True)
        ),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=list).encode()
    return GraphSnapshot(as_of=as_of, graph=graph, digest=hashlib.sha256(encoded).hexdigest())
