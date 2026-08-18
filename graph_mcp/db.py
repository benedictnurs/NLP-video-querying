from __future__ import annotations

import json
import os
from pathlib import Path

from neo4j import GraphDatabase
from neo4j.graph import Node, Path as GraphPath, Relationship
from neo4j.time import Date, DateTime, Time

from graph_mcp.media import localize_payload

ROOT = Path(__file__).resolve().parents[1]
_DRIVER = None


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def driver():
    global _DRIVER
    if _DRIVER is None:
        load_env()
        _DRIVER = GraphDatabase.driver(
            os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"),
            auth=(
                os.environ.get("NEO4J_USER", "neo4j"),
                os.environ.get("NEO4J_PASSWORD", "password"),
            ),
        )
    return _DRIVER


def run_cypher(cypher: str, params: dict | None = None, limit: int = 100) -> list[dict]:
    params = params or {}
    cap = max(1, min(int(limit), 200))
    with driver().session() as session:
        result = session.run(cypher, **params)
        rows = []
        for record in result:
            rows.append({key: to_json(record[key]) for key in record.keys()})
            if len(rows) >= cap:
                break
        return rows


def run_write(cypher: str, params: dict | None = None) -> dict:
    with driver().session() as session:
        result = session.run(cypher, **(params or {}))
        summary = result.consume()
        counters = summary.counters
        return {
            "nodes_created": counters.nodes_created,
            "nodes_deleted": counters.nodes_deleted,
            "properties_set": counters.properties_set,
            "relationships_created": counters.relationships_created,
            "relationships_deleted": counters.relationships_deleted,
        }


def to_json(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [to_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, Node):
        data = dict(value)
        data["_id"] = value.element_id
        data["_labels"] = sorted(value.labels)
        return data
    if isinstance(value, Relationship):
        data = dict(value)
        data["_id"] = value.element_id
        data["_type"] = value.type
        data["_start"] = value.start_node.element_id
        data["_end"] = value.end_node.element_id
        return data
    if isinstance(value, GraphPath):
        return {
            "nodes": [to_json(node) for node in value.nodes],
            "rels": [to_json(rel) for rel in value.relationships],
        }
    if isinstance(value, (DateTime, Date, Time)):
        return value.iso_format()
    return str(value)


def dumps(payload) -> str:
    return json.dumps(localize_payload(payload), indent=2, default=str)
