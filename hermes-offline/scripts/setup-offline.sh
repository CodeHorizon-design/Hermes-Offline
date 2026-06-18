#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Hermes Agent — Offline Edition  |  Linux / macOS One-Line Installer
#
# Usage (remote):
#   bash <(curl -fsSL https://raw.githubusercontent.com/CodeHorizon-design/Hermes-Offline/main/scripts/setup-offline.sh)
#
# Usage (local):
#   bash scripts/setup-offline.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_URL="https://github.com/CodeHorizon-design/Hermes-Offline"
SCRIPT_VERSION="1.0.0"

if [ -t 1 ]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; CYAN=''; BOLD=''; RESET=''
fi

log()  { echo -e "${BOLD}[hermes-offline]${RESET} $*"; }
ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET} $*"; }
err()  { echo -e "${RED}✗${RESET} $*"; }
ask()  { echo -en "${CYAN}?${RESET} $* "; }

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║   Hermes Agent — Offline Edition  v${SCRIPT_VERSION}            ║"
echo "  ║   Every feature · Zero cloud · Zero API keys        ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${RESET}"

# ─── 1. Python 3.11–3.13 ─────────────────────────────────────────────────────
log "Checking Python version..."
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cmd" &>/dev/null; then
    ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ] && [ "$minor" -lt 14 ]; then
      PYTHON="$cmd"
      ok "Python $ver ($cmd)"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  err "Python 3.11–3.13 required. Not found on PATH."
  if [[ "$OSTYPE" == "darwin"* ]] && command -v brew &>/dev/null; then
    ask "Install Python 3.12 via Homebrew? [Y/n]:"
    read -r ans
    if [[ "${ans:-Y}" =~ ^[Yy] ]]; then
      brew install python@3.12 && PYTHON="python3.12" && ok "Python 3.12 installed"
    fi
  fi
  if [ -z "$PYTHON" ]; then
    err "Install Python 3.11–3.13 from https://python.org and re-run."
    exit 1
  fi
fi

# ─── 2. uv ───────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  log "Installing uv (fast Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
  command -v uv &>/dev/null && ok "uv installed" || warn "uv not on PATH — falling back to pip"
else
  ok "uv $(uv --version 2>/dev/null | head -1)"
fi

_pip() {
  if command -v uv &>/dev/null; then uv pip install --system "$@"
  elif command -v pip3 &>/dev/null; then pip3 install "$@"
  else "$PYTHON" -m pip install "$@"; fi
}

# ─── 3. hermes-agent ─────────────────────────────────────────────────────────
log "Installing hermes-agent..."
_pip hermes-agent && ok "hermes-agent installed" || { err "hermes-agent install failed"; exit 1; }

# ─── 4. hermes-offline extension ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(dirname "$SCRIPT_DIR")"

log "Installing hermes-offline extension..."
if [ -f "$PACKAGE_DIR/pyproject.toml" ]; then
  _pip -e "$PACKAGE_DIR" 2>/dev/null && ok "hermes-offline (local) installed" || \
  { warn "Local install failed — trying PyPI..."; _pip hermes-offline || warn "hermes-offline install failed"; }
else
  _pip hermes-offline && ok "hermes-offline installed from PyPI" || warn "hermes-offline install failed"
fi

# ─── 5. Optional local tools ─────────────────────────────────────────────────
echo ""
log "Optional local tools (free, no API keys):"

command -v piper &>/dev/null || "$PYTHON" -c "import piper" 2>/dev/null && ok "piper-tts already available" || {
  ask "Install piper-tts (local TTS, <100 MB)? [Y/n]:"; read -r ans
  [[ "${ans:-Y}" =~ ^[Yy] ]] && { _pip piper-tts && ok "piper-tts installed" || warn "piper-tts skipped"; }
}

"$PYTHON" -c "import faster_whisper" 2>/dev/null && ok "faster-whisper already available" || {
  ask "Install faster-whisper (local voice transcription)? [Y/n]:"; read -r ans
  [[ "${ans:-Y}" =~ ^[Yy] ]] && { _pip faster-whisper && ok "faster-whisper installed" || warn "faster-whisper skipped"; }
}

"$PYTHON" -c "import sqlite_vec" 2>/dev/null && ok "sqlite-vec already available" || {
  ask "Install sqlite-vec (local semantic memory, ~1 MB)? [Y/n]:"; read -r ans
  [[ "${ans:-Y}" =~ ^[Yy] ]] && { _pip sqlite-vec && ok "sqlite-vec installed" || warn "sqlite-vec skipped"; }
}

# ─── 6. Ollama ────────────────────────────────────────────────────────────────
echo ""
log "Checking Ollama..."
if command -v ollama &>/dev/null; then
  ok "Ollama $(ollama --version 2>/dev/null | head -1)"
  if ! curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
    log "Starting Ollama server..."
    ollama serve &>/dev/null &
    sleep 3
    curl -sf http://127.0.0.1:11434/api/tags &>/dev/null && ok "Ollama server started" || \
      warn "Could not start Ollama. Run 'ollama serve' in another terminal."
  else
    ok "Ollama server already running"
  fi
else
  warn "Ollama not found."
  ask "Install Ollama (required for offline mode)? [Y/n]:"; read -r ans
  if [[ "${ans:-Y}" =~ ^[Yy] ]]; then
    curl -fsSL https://ollama.com/install.sh | sh && ok "Ollama installed" && \
      ollama serve &>/dev/null & sleep 3 || err "Ollama install failed. Visit https://ollama.com"
  fi
fi

# ─── 7. Offline setup wizard ─────────────────────────────────────────────────
echo ""
log "Running offline setup wizard (hardware detection → model recommendation → config)..."
echo ""
command -v hermes-offline-setup &>/dev/null && hermes-offline-setup || \
  warn "hermes-offline-setup not found. Run manually: hermes-offline-setup"

# ─── 8. Post-install parity check ────────────────────────────────────────────
echo ""
log "Running quick feature parity check..."
command -v hermes-offline-test-parity &>/dev/null && \
  hermes-offline-test-parity --quick --no-color || \
  warn "hermes-offline-test-parity not found. Run manually after install."

# ─── 9. Done ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔═══════════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}${BOLD}║   Hermes Agent (Offline Edition) is ready!           ║${RESET}"
echo -e "${GREEN}${BOLD}╚═══════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${CYAN}hermes-offline${RESET}               Start an offline session"
echo -e "  ${CYAN}hermes-offline --tui${RESET}          Launch the TUI interface"
echo -e "  ${CYAN}hermes-offline --think${RESET}        Enable Qwen3 chain-of-thought"
echo -e "  ${CYAN}hermes-offline update${RESET}         Check for updates"
echo -e "  ${CYAN}hermes-offline-bench${RESET}          Benchmark your model"
echo -e "  ${CYAN}hermes-offline-status${RESET}         Show all component status"
echo -e "  ${CYAN}hermes-offline-test-parity${RESET}    Run full feature parity tests"
echo ""
echo "  Config:  ~/.hermes/config.yaml"
echo "  Memory:  ~/.hermes/memories/"
echo "  Skills:  ~/.hermes/skills/"
echo "  Docs:    ${REPO_URL}"
echo ""
