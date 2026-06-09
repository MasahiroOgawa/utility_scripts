#!/usr/bin/env bash
# Launch Claude Code through claude-code-router with two backends:
#   - Ollama (local, default) — no key, no quota, works offline
#   - Gemini (free tier)      — faster but flaky; use --cloud to default to it
#
# Claude Code's `/model` arrow picker only lists Claude's own models and
# can NOT be extended to show ollama/gemini entries — that picker is
# Claude Code internal. To switch backend at runtime, TYPE the full
# string (no arrow picker), e.g.:
#   /model ollama,qwen2.5-coder:14b
#   /model ollama,deepseek-r1:14b
#   /model gemini,gemini-2.5-flash
#   /model gemini,gemini-2.5-pro
#
# Reads the Gemini API key from .env (GEMINI_API_KEY, falling back to
# ANTHROPIC_API_KEY) and rewrites ~/.claude-code-router/config.json on
# every run, so .env stays the single source of truth.
#
# One-time install: bun install -g @musistudio/claude-code-router
# Usage:
#   ./script/claude_router.sh           # local Ollama as default
#   ./script/claude_router.sh --cloud   # Gemini as default (needs a key)

set -euo pipefail

BACKEND=ollama
PASSTHROUGH_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --cloud|--gemini) BACKEND=gemini ;;
    --local|--ollama) BACKEND=ollama ;;
    *) PASSTHROUGH_ARGS+=("$arg") ;;
  esac
done

# Resolve through symlinks so this works when invoked via e.g. ~/bin/claude_router.sh.
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV_FILE="$REPO_ROOT/.env"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: $ENV_FILE missing — copy .env.example and add your Gemini key." >&2; exit 1; }

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

GEMINI_API_KEY="${GEMINI_API_KEY:-${ANTHROPIC_API_KEY:-}}"
export GEMINI_API_KEY

command -v ccr >/dev/null || { echo "ERROR: ccr not on PATH. Install once with: bun install -g @musistudio/claude-code-router" >&2; exit 1; }

OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_REACHABLE=1
curl -sS -m 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1 || OLLAMA_REACHABLE=0

if [[ "$BACKEND" == "ollama" && "$OLLAMA_REACHABLE" -eq 0 ]]; then
  echo "ERROR: Ollama not reachable at $OLLAMA_URL but --local backend selected. Start ollama (e.g. 'ollama serve') or pass --cloud." >&2
  exit 1
fi
if [[ "$BACKEND" == "gemini" && -z "$GEMINI_API_KEY" ]]; then
  echo "ERROR: GEMINI_API_KEY not set in $ENV_FILE but --cloud backend selected." >&2
  exit 1
fi
[[ "$OLLAMA_REACHABLE" -eq 1 ]] || echo "WARN: Ollama not reachable at $OLLAMA_URL — typed '/model ollama,...' switches will fail until you start it." >&2

# Strip ANTHROPIC_* so the underlying `claude` CLI doesn't try to call
# api.anthropic.com directly — the router owns upstream auth from here on.
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN

if [[ "$BACKEND" == "gemini" ]]; then
  # All Gemini routes pinned to FLASH on purpose: gemini-2.5-pro on the free
  # tier rate-limits at ~2 req/min and ccr then retries 10× with a 30s
  # timeout each, producing "attempt */10 TIMEOUT_MS=..." that looks like a
  # network hang. Flash is ~15 req/min on the free tier and handles
  # Claude Code's thinking/long-context turns just fine. Switch a single
  # route to gemini-2.5-pro manually at runtime when you actually need it:
  #   /model gemini,gemini-2.5-pro
  DEFAULT_MODEL="gemini,gemini-2.5-flash"
  THINK_MODEL="gemini,gemini-2.5-flash"
  LONG_MODEL="gemini,gemini-2.5-flash"
  WEB_MODEL="gemini,gemini-2.5-flash"
  BACKGROUND_MODEL="ollama,qwen2.5-coder:14b"
else
  # Default to llama3.1:8b rather than qwen2.5-coder: qwen is heavily
  # tool/function-call-tuned and answers casual prompts like "hello" by
  # inventing fake tool calls (e.g. `{"name":"greet","arguments":{...}}`)
  # because Claude Code's system prompt is dense with tool definitions.
  # llama3.1 is more chat-balanced. Switch to qwen at runtime for heavy
  # coding turns: /model ollama,qwen2.5-coder:14b
  # deepseek-r1 does NOT support tool/function calling, so it can't be
  # used as a Claude Code default (file/shell ops break); /model into it
  # only for plain Q&A: /model ollama,deepseek-r1:14b
  DEFAULT_MODEL="ollama,llama3.1:8b"
  THINK_MODEL="ollama,llama3.1:8b"
  LONG_MODEL="ollama,llama3.1:8b"
  WEB_MODEL="ollama,llama3.1:8b"
  BACKGROUND_MODEL="ollama,llama3.1:8b"
fi

CCR_DIR="$HOME/.claude-code-router"
mkdir -p "$CCR_DIR"

# Custom transformer with two distinct jobs depending on the destination
# model — Ollama and Gemini both need a tweak, but in opposite directions:
#
# Ollama (OpenAI-compatible endpoint): rejects ANY request whose body
# contains `reasoning.effort` on a non-thinking model (qwen, gemma,
# llama3.1) with: `"<model>" does not support thinking`. ccr's
# Anthropic→OpenAI converter ALWAYS sets `reasoning: { effort, enabled }`
# when Claude Code sends `thinking`, and the built-in `reasoning`
# transformer can only ADD fields, not strip them — so we delete the
# offending fields outright before the request leaves the router.
#
# Gemini-2.5-flash: when `thinking` is passed through, flash sometimes
# streams only reasoning_tokens and emits zero text content blocks
# before `end_turn` — Claude Code then renders a bare "Thought for Xs"
# with no answer. We want to force thinking-off. We CANNOT `delete
# req.thinking` here because ccr's gemini transformer reads
# `req.thinking` to build Gemini's `thinkingConfig`, and a missing field
# crashes the transformer with `Cannot read properties of undefined
# (reading 'filter')` → HTTP 500 → Claude Code's "TIMEOUT_MS" retry loop.
# So for Gemini we REPLACE `req.thinking` with `{ type: "disabled" }`
# instead of removing it — preserves the shape, signals no thinking.
#
# deepseek-r1 is omitted from the Ollama side because it supports
# thinking natively.
cat > "$CCR_DIR/strip-reasoning.js" <<'JS'
class StripReasoning {
  constructor(options = {}) {
    this.name = "stripreasoning";
    this.options = options;
  }
  async transformRequestIn(req) {
    delete req.reasoning;
    delete req.reasoning_effort;
    delete req.enable_thinking;
    const model = typeof req.model === "string" ? req.model : "";
    if (model.startsWith("gemini")) {
      req.thinking = { type: "disabled" };
    } else {
      delete req.thinking;
    }
    return req;
  }
  async transformResponseOut(res) { return res; }
}
module.exports = StripReasoning;
JS
# `$GEMINI_API_KEY` is written literally so claude-code-router resolves
# it from the env we exported above (key never lands in the file on disk).
# The ollama provider uses a dummy "api_key": "ollama" because Ollama's
# OpenAI-compatible endpoint ignores auth but the router requires the field.
#
# The per-model `stripreasoning` transformer (defined above in
# strip-reasoning.js) is REQUIRED for qwen/gemma/llama: ccr's
# Anthropic→OpenAI converter inserts `reasoning: { effort, enabled }`
# whenever Claude Code sends a `thinking` block, and Ollama rejects ANY
# `reasoning.effort` value on a non-thinking model with the cryptic
# error: `"<model>" does not support thinking`. Built-in transformers
# (`reasoning`, `customparams`) can only ADD/merge fields — they can't
# delete `reasoning.effort` — so we ship a tiny custom plugin to do it.
# deepseek-r1 is omitted because it DOES support thinking natively.
#
# gemini-2.5-flash also gets `stripreasoning` for a different reason:
# when Claude Code's `thinking` block is translated through to Flash, the
# model sometimes streams reasoning_tokens only and emits zero text
# content blocks before `end_turn` — Claude Code then renders just
# "Thought for Xs" with no answer. Flash still reasons internally by
# default (Gemini 2.5 thinking is built-in), so stripping the explicit
# instruction just removes the failure mode. gemini-2.5-pro is left
# untouched: its thinking is stable, the free-tier rate limit (~2 RPM)
# already discourages casual use, and on the hard turns where you
# manually switch to pro you want full extended-thinking active.
cat > "$CCR_DIR/config.json" <<JSON
{
  "LOG": true,
  "transformers": [
    { "path": "$CCR_DIR/strip-reasoning.js" }
  ],
  "Providers": [
    {
      "name": "gemini",
      "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models/",
      "api_key": "\$GEMINI_API_KEY",
      "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
      "transformer": {
        "use": ["gemini"],
        "gemini-2.5-flash": { "use": ["stripreasoning", "gemini"] }
      }
    },
    {
      "name": "ollama",
      "api_base_url": "http://localhost:11434/v1/chat/completions",
      "api_key": "ollama",
      "models": ["deepseek-r1:14b", "qwen2.5-coder:14b", "gemma3:12b", "llama3.1:8b"],
      "transformer": {
        "qwen2.5-coder:14b": { "use": ["stripreasoning"] },
        "gemma3:12b":        { "use": ["stripreasoning"] },
        "llama3.1:8b":       { "use": ["stripreasoning"] }
      }
    }
  ],
  "Router": {
    "default":     "$DEFAULT_MODEL",
    "background":  "$BACKGROUND_MODEL",
    "think":       "$THINK_MODEL",
    "longContext": "$LONG_MODEL",
    "webSearch":   "$WEB_MODEL"
  }
}
JSON
chmod 600 "$CCR_DIR/config.json"

cat >&2 <<BANNER
─────────────────────────────────────────────────────────────
claude-code-router · default backend: $BACKEND
  default     → $DEFAULT_MODEL
  think       → $THINK_MODEL
  longContext → $LONG_MODEL

Claude Code's /model picker can't list these — TYPE the full string:
  /model ollama,llama3.1:8b         (tools OK — chat-balanced default)
  /model ollama,qwen2.5-coder:14b   (tools OK — heavy coding; invents fake tool calls on casual chat)
  /model ollama,gemma3:12b          (no tools)
  /model ollama,deepseek-r1:14b     (no tools — answers only, no file/shell)
  /model gemini,gemini-2.5-flash    (15 RPM free tier — thinking forced off)
  /model gemini,gemini-2.5-pro      (~2 RPM free — use sparingly, thinking on)
─────────────────────────────────────────────────────────────
BANNER

# Restart the router so it picks up the freshly written config.
ccr stop >/dev/null 2>&1 || true

exec ccr code "${PASSTHROUGH_ARGS[@]}"
