#!/usr/bin/env bash
# Apply an optimized Ollama Modelfile for agentic use.
# Usage: bash apply-modelfile.sh [model_size]
#   model_size: 8b (default), 4b, 1.7b, llama31

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MF_DIR="$SCRIPT_DIR/../hermes_offline/modelfiles"
SIZE="${1:-8b}"

case "$SIZE" in
  8b)    MF="$MF_DIR/qwen3-8b-agent.Modelfile";    NAME="qwen3-8b-agent"    ;;
  4b)    MF="$MF_DIR/qwen3-4b-agent.Modelfile";    NAME="qwen3-4b-agent"    ;;
  1.7b)  MF="$MF_DIR/qwen3-1.7b-agent.Modelfile";  NAME="qwen3-1.7b-agent"  ;;
  llama) MF="$MF_DIR/llama31-8b-agent.Modelfile";  NAME="llama31-8b-agent"  ;;
  *)
    echo "Unknown size: $SIZE. Use: 8b | 4b | 1.7b | llama"
    exit 1
    ;;
esac

if [ ! -f "$MF" ]; then
    echo "Modelfile not found: $MF"
    exit 1
fi

echo "Creating Ollama model: $NAME"
ollama create "$NAME" -f "$MF"
echo "Done. Use with: hermes model → $NAME"
