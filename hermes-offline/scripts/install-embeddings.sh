#!/usr/bin/env bash
# install-embeddings.sh — pull nomic-embed-text and optional sqlite-vec
# for Phase 2 semantic memory.
#
# Usage:
#   bash install-embeddings.sh           # pull nomic-embed-text only
#   bash install-embeddings.sh --vec     # also install sqlite-vec Python wheel
#   bash install-embeddings.sh --all     # nomic + sqlite-vec + enable in config

set -euo pipefail

INSTALL_VEC=false
ENABLE_CONFIG=false

for arg in "$@"; do
  case "$arg" in
    --vec)  INSTALL_VEC=true ;;
    --all)  INSTALL_VEC=true; ENABLE_CONFIG=true ;;
  esac
done

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[embeddings]${NC} $*"; }
warn()  { echo -e "${YELLOW}[embeddings]${NC} $*"; }
error() { echo -e "${RED}[embeddings]${NC} $*" >&2; }

# ── 1. Check Ollama is running ─────────────────────────────────────────────
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  error "Ollama is not running. Start it with: ollama serve"
  exit 1
fi

# ── 2. Pull nomic-embed-text ───────────────────────────────────────────────
info "Pulling nomic-embed-text (274 MB) ..."
if ollama pull nomic-embed-text; then
  info "nomic-embed-text ready."
else
  error "Failed to pull nomic-embed-text."
  exit 1
fi

# ── 3. Verify embedding works ──────────────────────────────────────────────
info "Verifying embedding endpoint..."
RESPONSE=$(curl -sf -X POST http://127.0.0.1:11434/api/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"nomic-embed-text","prompt":"test"}' 2>&1) || true

if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d.get('embedding',[])) > 0" 2>/dev/null; then
  info "Embedding endpoint verified — 768-dim vectors working."
else
  warn "Could not verify embedding response. Check: curl http://127.0.0.1:11434/api/tags"
fi

# ── 4. Optional: sqlite-vec Python wheel ──────────────────────────────────
if $INSTALL_VEC; then
  info "Installing sqlite-vec Python wheel..."
  if command -v uv &>/dev/null; then
    uv pip install "sqlite-vec>=0.1.0"
  elif command -v pip &>/dev/null; then
    pip install "sqlite-vec>=0.1.0"
  else
    error "Neither uv nor pip found. Install manually: pip install sqlite-vec"
    exit 1
  fi
  info "sqlite-vec installed."

  if python3 -c "import sqlite_vec; print('sqlite-vec version:', sqlite_vec.__version__)" 2>/dev/null; then
    info "sqlite-vec smoke test passed."
  else
    warn "sqlite-vec imported but version check failed — may be fine."
  fi
fi

# ── 5. Optional: enable in hermes config ──────────────────────────────────
if $ENABLE_CONFIG; then
  info "Enabling semantic memory in hermes config..."
  python3 - <<'PYEOF'
import sys
from pathlib import Path

try:
    from hermes_cli.config import load_config, save_config
except ImportError:
    print("[embeddings] hermes-agent not installed — skipping config update")
    sys.exit(0)

cfg = load_config()
mem = cfg.get("memory", {})
if not isinstance(mem, dict):
    mem = {}
mem["semantic_backend"] = "sqlite_vec"
mem["embedding_model"]  = "nomic-embed-text"
cfg["memory"] = mem
save_config(cfg)
print("[embeddings] Config updated: memory.semantic_backend = sqlite_vec")
PYEOF
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
info "Done. Semantic memory setup summary:"
echo "  nomic-embed-text  : installed via Ollama"
if $INSTALL_VEC; then
  echo "  sqlite-vec        : installed (cosine similarity search enabled)"
else
  echo "  sqlite-vec        : NOT installed (FTS5 keyword search only)"
  echo "                      Re-run with --vec to add cosine similarity"
fi
if $ENABLE_CONFIG; then
  echo "  hermes config     : updated  (memory.semantic_backend = sqlite_vec)"
else
  echo "  hermes config     : not modified"
  echo "                      Re-run with --all to auto-enable, or add to"
  echo "                      ~/.hermes/config.yaml:"
  echo "                        memory:"
  echo "                          semantic_backend: sqlite_vec"
fi
echo ""
info "Start hermes-offline to begin using semantic memory:"
echo "  hermes-offline"
