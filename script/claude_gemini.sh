#!/usr/bin/env bash
# Launch Claude Code through claude-code-router so requests are routed to
# Google Gemini (free tier) instead of Anthropic. Lets contributors use
# Claude Code on this repo without a paid Anthropic subscription.
#
# Reads the Gemini API key from .env (ANTHROPIC_API_KEY, kept under that
# name for backward-compatibility with the rest of the repo) and rewrites
# ~/.claude-code-router/config.json on every run, so .env stays the single
# source of truth. Other repos keep using plain `claude` unchanged.
#
# One-time install: bun install -g @musistudio/claude-code-router
# Usage:            ./script/claude_gemini.sh [args forwarded to `ccr code`]

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
    }
  ],
  "Router": {
    "default":     "gemini,gemini-2.5-flash",
    "background":  "gemini,gemini-2.5-flash",
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
