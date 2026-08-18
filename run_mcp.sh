#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$ROOT"
export NEO4J_URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
export DEFINITIONS_PATH="${DEFINITIONS_PATH:-$ROOT/definitions.yaml}"
export DATA_DIR="${DATA_DIR:-$ROOT/data}"
exec "$ROOT/.venv/bin/python" -m graph_mcp
