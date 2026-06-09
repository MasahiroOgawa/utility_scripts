#!/usr/bin/env bash
# Launch Claude Code through claude-code-router with two backends:
#   - Gemini (free tier, default)        — fast, cloud, needs a key
#   - Ollama (local, no key needed)      — fallback when Gemini 503s or
#                                          when you're offline
#
# Switch live inside Claude Code by typing (don't use the arrow picker):
#   /model gemini,gemini-2.5-flash
#   /model gemini,gemini-2.5-pro
#   /model ollama,deepseek-r1:14b
#   /model ollama,qwen2.5-coder:14b
#
# Reads the Gemini API key from .env (ANTHROPIC_API_KEY, kept under that
# name for backward-compatibility with the rest of the repo) and rewrites
# ~/.claude-code-router/config.json on every run, so .env stays the single
# source of truth. Other repos keep using plain `claude` unchanged.
#
# One-time install: bun install -g @musistudio/claude-code-router
# Usage:            ./script/claude_router.sh [args forwarded to `ccr code`]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV_FILE="$REPO_ROOT/.env"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: $ENV_FILE missing — copy .env.example and add your Gemini key." >&2; exit 1; }

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a
[[ -n "${ANTHROPIC_API_KEY:-}" ]] || { echo "ERROR: ANTHROPIC_API_KEY not set in $ENV_FILE (paste your Gemini AIza… key there)." >&2; exit 1; }

command -v ccr >/dev/null || { echo "ERROR: ccr not on PATH. Install once with: bun install -g @musistudio/claude-code-router" >&2; exit 1; }

# Ollama is optional — warn but don't fail, so cloud-only users still work.
OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
if ! curl -sS -m 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  echo "WARN: Ollama not reachable at $OLLAMA_URL — local /model switches will fail until you start it." >&2
fi

# Remap to the name the router config references, then strip ANTHROPIC_*
# so the underlying `claude` CLI doesn't try to call api.anthropic.com
# directly — the router owns upstream auth from here on. ANTHROPIC_MODEL
# is kept so .env can pin the display name (e.g. gemini-2.5-flash) shown
# in Claude Code's status line.
export GEMINI_API_KEY="$ANTHROPIC_API_KEY"
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN

CCR_DIR="$HOME/.claude-code-router"
mkdir -p "$CCR_DIR"
# Heredoc is single-quoted on purpose: `$GEMINI_API_KEY` must reach the
# config file literally so claude-code-router resolves it at load time
# from the env we exported above (key never lands in the file on disk).
# The ollama provider uses a dummy "api_key": "ollama" because Ollama's
# OpenAI-compatible endpoint ignores auth but the router requires the
# field to be present.
cat > "$CCR_DIR/config.json" <<'JSON'
{
  "LOG": false,
  "Providers": [
    {
      "name": "gemini",
      "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models/",
      "api_key": "$GEMINI_API_KEY",
      "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
      "transformer": { "use": ["gemini"] }
    },
    {
      "name": "ollama",
      "api_base_url": "http://localhost:11434/v1/chat/completions",
      "api_key": "ollama",
      "models": ["deepseek-r1:14b", "qwen2.5-coder:14b", "gemma3:12b", "llama3.1:8b"]
    }
  ],
  "Router": {
    "default":     "gemini,gemini-2.5-flash",
    "background":  "ollama,qwen2.5-coder:14b",
    "think":       "gemini,gemini-2.5-pro",
    "longContext": "gemini,gemini-2.5-pro",
    "webSearch":   "gemini,gemini-2.5-flash"
  }
}
JSON
chmod 600 "$CCR_DIR/config.json"

# Restart the router so it picks up the freshly written config.
ccr stop >/dev/null 2>&1 || true

exec ccr code "$@"
