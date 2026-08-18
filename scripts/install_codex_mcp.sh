#!/usr/bin/env bash
# Install this repo's graph MCP (Code Four) into Codex
# (CLI, IDE extension, ChatGPT desktop share the same config.toml).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="code-four"
SCOPE="user"
NEO4J_URI="${NEO4J_URI:-bolt://127.0.0.1:7687}"
SKIP_VENV=0
DRY_RUN=0
REMOVE=0
CHECK_IMPORT=1
RENAME_FROM=()
APPROVAL="auto"
CODEX_ALLOW_ALL=0

usage() {
  cat <<EOF
Install or rename the Code Four MCP server for Codex.

Usage:
  $(basename "$0") [options]

Options:
  --project              Write .codex/config.toml in this repo (trusted projects only)
  --user                 Write \$CODEX_HOME/config.toml or ~/.codex/config.toml (default)
  --name NAME            Server name to install as (default: code-four)
  --rename-from OLD      Remove an old server name while installing. Repeatable.
                         If you previously installed video-intel-graph, this is done
                         automatically when the new name is code-four.
  --neo4j-uri URI        Bolt URI (default: bolt://127.0.0.1:7687)
  --approval MODE        MCP tool approval: auto (always allow, default),
                         prompt, writes, or approve
  --codex-allow-all      Also set Codex-wide approval_policy = "never"
                         so shell/sandbox prompts are skipped too
  --skip-venv            Do not create .venv or pip install
  --dry-run              Print the config block; do not write files
  --remove               Remove this MCP server from the Codex config
  --help                 Show this help

Examples:
  ./scripts/install_codex_mcp.sh
  ./scripts/install_codex_mcp.sh --codex-allow-all
  ./scripts/install_codex_mcp.sh --name code-four --rename-from video-intel-graph
  ./scripts/install_codex_mcp.sh --project

Codex-wide always-allow (all tools, not just this MCP) is this line in
~/.codex/config.toml:

  approval_policy = "never"

This script always writes default_tools_approval_mode = "auto" for Code Four
unless you pass --approval prompt|writes|approve.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) SCOPE="project"; shift ;;
    --user) SCOPE="user"; shift ;;
    --name) NAME="${2:?}"; shift 2 ;;
    --rename-from) RENAME_FROM+=("${2:?}"); shift 2 ;;
    --neo4j-uri) NEO4J_URI="${2:?}"; shift 2 ;;
    --approval)
      APPROVAL="${2:?}"
      case "$APPROVAL" in
        auto|prompt|writes|approve) ;;
        *)
          echo "Unknown --approval $APPROVAL (use auto, prompt, writes, or approve)" >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --codex-allow-all) CODEX_ALLOW_ALL=1; shift ;;
    --skip-venv) SKIP_VENV=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --remove) REMOVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$SCOPE" == "project" ]]; then
  CONFIG="$ROOT/.codex/config.toml"
else
  CONFIG="${CODEX_HOME:-$HOME/.codex}/config.toml"
fi

PYTHON="$ROOT/.venv/bin/python"
LAUNCHER="$ROOT/run_mcp.sh"
DEFINITIONS="$ROOT/definitions.yaml"
DATA_DIR="$ROOT/data"

if [[ "$NAME" != "video-intel-graph" ]]; then
  has_legacy=0
  for old in "${RENAME_FROM[@]+"${RENAME_FROM[@]}"}"; do
    if [[ "$old" == "video-intel-graph" ]]; then
      has_legacy=1
      break
    fi
  done
  if [[ "$has_legacy" -eq 0 ]]; then
    RENAME_FROM+=("video-intel-graph")
  fi
fi

REMOVE_NAMES="$NAME"
for old in "${RENAME_FROM[@]+"${RENAME_FROM[@]}"}"; do
  if [[ -n "$old" && "$old" != "$NAME" ]]; then
    REMOVE_NAMES+=",$old"
  fi
done

upsert_toml() {
  ACTION="$1" NAME="$NAME" CODEX_CONFIG="$CONFIG" MCP_COMMAND="$LAUNCHER" MCP_CWD="$ROOT" \
  MCP_PYTHONPATH="$ROOT" MCP_NEO4J_URI="$NEO4J_URI" MCP_DEFINITIONS="$DEFINITIONS" MCP_DATA_DIR="$DATA_DIR" \
  MCP_REMOVE_NAMES="$REMOVE_NAMES" MCP_APPROVAL="$APPROVAL" MCP_CODEX_ALLOW_ALL="$CODEX_ALLOW_ALL" \
  python3 - <<'PY'
import json, os, pathlib, sys

name = os.environ["NAME"]
action = os.environ["ACTION"]
path = pathlib.Path(os.environ["CODEX_CONFIG"])
command = os.environ["MCP_COMMAND"]
cwd = os.environ["MCP_CWD"]
env = {
    "PYTHONPATH": os.environ["MCP_PYTHONPATH"],
    "NEO4J_URI": os.environ["MCP_NEO4J_URI"],
    "DEFINITIONS_PATH": os.environ["MCP_DEFINITIONS"],
    "DATA_DIR": os.environ["MCP_DATA_DIR"],
}
remove_names = [
    item.strip()
    for item in os.environ.get("MCP_REMOVE_NAMES", "").split(",")
    if item.strip()
]
approval = os.environ.get("MCP_APPROVAL") or "auto"
codex_allow_all = os.environ.get("MCP_CODEX_ALLOW_ALL") == "1"

def is_server_table(header: str, server: str) -> bool:
    inner = header.strip()[1:-1]
    keys = (f"mcp_servers.{server}", f'mcp_servers."{server}"')
    return any(inner == key or inner.startswith(key + ".") for key in keys)

def tables_for(text: str, server: str) -> str:
    lines = text.splitlines(keepends=True)
    out = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skip = is_server_table(stripped, server)
        if not skip:
            out.append(line)
    return "".join(out).rstrip() + ("\n" if out else "")

def strip_names(text: str, names: list[str]) -> str:
    for server in names:
        text = tables_for(text, server)
    return text

def set_codex_allow_all(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out = []
    in_table = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = True
        if not in_table and stripped.startswith("approval_policy"):
            if not replaced:
                out.append('approval_policy = "never"\n')
                replaced = True
            continue
        out.append(line)
    text = "".join(out)
    if replaced:
        return text
    prefix = 'approval_policy = "never"\n'
    if text.strip():
        return prefix + "\n" + text.lstrip("\n")
    return prefix


def block() -> str:
    env_rows = "\n".join(f'{key} = {json.dumps(value)}' for key, value in env.items())
    return (
        f"[mcp_servers.{name}]\n"
        f"command = {json.dumps(command)}\n"
        f"cwd = {json.dumps(cwd)}\n"
        f"startup_timeout_sec = 30\n"
        f"tool_timeout_sec = 120\n"
        f"default_tools_approval_mode = {json.dumps(approval)}\n"
        f"\n"
        f"[mcp_servers.{name}.env]\n"
        f"{env_rows}\n"
    )

if action == "print":
    extras = [item for item in remove_names if item != name]
    if extras:
        sys.stdout.write(f"# would remove: {', '.join(extras)}\n")
    if codex_allow_all:
        sys.stdout.write('approval_policy = "never"\n\n')
    sys.stdout.write(block())
    raise SystemExit(0)

text = path.read_text() if path.exists() else ""
text = strip_names(text, remove_names)
if action == "remove":
    path.parent.mkdir(parents=True, exist_ok=True)
    if text.strip():
        path.write_text(text.rstrip() + "\n")
    elif path.exists():
        path.write_text("")
    print(f"Removed [{', '.join(remove_names)}] from {path}")
    raise SystemExit(0)

if codex_allow_all:
    text = set_codex_allow_all(text)

if text and not text.endswith("\n"):
    text += "\n"
if text.strip():
    text = text.rstrip() + "\n\n"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(text + block())
dropped = [item for item in remove_names if item != name]
note = f" (removed {', '.join(dropped)})" if dropped else ""
print(f"Wrote {path} as [{name}]{note}")
PY
}

if [[ "$REMOVE" -eq 1 ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Would remove [$NAME] from $CONFIG"
    exit 0
  fi
  upsert_toml remove
  if command -v codex >/dev/null 2>&1 && [[ "$SCOPE" == "user" ]]; then
    CODEX_HOME="${CODEX_HOME:-$HOME/.codex}" codex mcp list || true
  fi
  exit 0
fi

if [[ "$SKIP_VENV" -eq 0 && "$DRY_RUN" -eq 0 ]]; then
  if [[ ! -x "$PYTHON" ]]; then
    echo "Creating $ROOT/.venv"
    python3 -m venv "$ROOT/.venv"
  fi
  echo "Installing MCP Python deps into .venv"
  "$ROOT/.venv/bin/pip" install -q -U pip
  "$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements-mcp.txt"
fi

chmod +x "$LAUNCHER"

if [[ "$CHECK_IMPORT" -eq 1 && "$DRY_RUN" -eq 0 && -x "$PYTHON" ]]; then
  echo "Checking graph_mcp import"
  PYTHONPATH="$ROOT" "$PYTHON" -c "from graph_mcp.server import mcp"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Would write $CONFIG"
  echo
  upsert_toml print
  exit 0
fi

if [[ -f "$CONFIG" ]]; then
  stamp="$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG" "$CONFIG.bak-$stamp"
  echo "Backed up $CONFIG -> $CONFIG.bak-$stamp"
fi

upsert_toml upsert

echo
echo "Codex MCP server '$NAME' is installed."
echo "  config: $CONFIG"
echo "  launch: $LAUNCHER"
echo "  neo4j:  $NEO4J_URI"
echo "  mcp approval: $APPROVAL (all Code Four tools)"
if [[ "$CODEX_ALLOW_ALL" -eq 1 ]]; then
  echo "  codex approval_policy: never"
fi
if [[ "$REMOVE_NAMES" != "$NAME" ]]; then
  echo "  renamed from: ${REMOVE_NAMES#"$NAME,"}"
fi
echo
echo "Restart Codex (CLI / IDE extension / ChatGPT desktop) so it reloads MCP."
echo "Then in a session: call explain_graph_context first, then list_query_blocks / run_blocks."
echo "Neo4j must be reachable at $NEO4J_URI."

if command -v codex >/dev/null 2>&1 && [[ "$SCOPE" == "user" ]]; then
  echo
  CODEX_HOME="$(cd "$(dirname "$CONFIG")" && pwd)"
  export CODEX_HOME
  if ! codex mcp get "$NAME"; then
    echo "Config written, but 'codex mcp get' could not read it. Open $CONFIG if Codex is using another CODEX_HOME." >&2
  fi
fi
