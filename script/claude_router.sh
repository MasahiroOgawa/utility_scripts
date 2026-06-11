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

# ccr spawns the `claude` CLI via `child_process.spawn("claude", ..., {shell:true})`,
# which goes through `/bin/sh -c "claude ..."`. If claude was installed under a
# Node-version-managed prefix (nvm, fnm, asdf) that is only added to PATH by
# the user's interactive shell rc, sh fails to resolve `claude` and dies with
# `/bin/sh: 1: claude: not found` — regardless of what the parent script's
# PATH looks like. ccr honors $CLAUDE_PATH as an absolute-path override; set
# it from `command -v` so the spawn never has to do its own PATH lookup.
CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
[[ -n "$CLAUDE_BIN" ]] || { echo "ERROR: 'claude' CLI not on PATH. Install once with: bun install -g @anthropic-ai/claude-code" >&2; exit 1; }
export CLAUDE_PATH="$CLAUDE_BIN"

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
# (ANTHROPIC_MODEL is intentionally kept and re-set below so Claude Code's
# title bar reflects the current default route instead of stale state.)
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
  # Default to qwen3:14b: newer generation than qwen2.5-coder, chat-balanced
  # (so casual prompts like "hello" no longer get answered with invented tool
  # calls), supports tool/function calling, and fits comfortably in 12 GB
  # VRAM. Background route stays on llama3.1:8b to keep small/fast model
  # warm for title-generation and summary subtasks.
  # qwen2.5-coder:14b is still reachable for heavy coding turns via
  #   /model ollama,qwen2.5-coder:14b
  # deepseek-r1 does NOT reliably emit tool calls, so it can't be used as
  # a Claude Code default (file/shell ops break); /model into it only for
  # plain Q&A:
  #   /model ollama,deepseek-r1:14b
  DEFAULT_MODEL="ollama,qwen3:14b"
  THINK_MODEL="ollama,qwen3:14b"
  LONG_MODEL="ollama,qwen3:14b"
  WEB_MODEL="ollama,qwen3:14b"
  BACKGROUND_MODEL="ollama,llama3.1:8b"
fi

# Claude Code's title bar / startup banner shows whatever model name it has
# cached — without an explicit hint it keeps showing the model from the
# previous session (e.g. "gemini-2.5-flash" after a --cloud run, even when
# you've since restarted under --local). Set ANTHROPIC_MODEL to the bare
# model id (provider prefix dropped — Claude Code only renders the second
# half) so the banner matches the route the router is actually using.
# This is cosmetic only — ccr routes by its own Router config, not by the
# model name in the incoming request.
DEFAULT_MODEL_NAME="${DEFAULT_MODEL##*,}"
export ANTHROPIC_MODEL="$DEFAULT_MODEL_NAME"

CCR_DIR="$HOME/.claude-code-router"
mkdir -p "$CCR_DIR"

# Custom transformer for Ollama models only. ccr's Anthropic→OpenAI
# converter inserts a NESTED `reasoning: { effort, enabled }` object
# whenever Claude Code sends a `thinking` block. That nested form is
# poison for the Ollama OpenAI-compatible endpoint two different ways:
#
#   1. Non-thinking models (qwen2.5-coder, gemma3, llama3.1) REJECT it
#      outright with the cryptic `"<model>" does not support thinking`.
#   2. Thinking models (qwen3) ACCEPT it but then run thinking at full
#      effort — and under Claude Code's huge system prompt qwen3 spends
#      its ENTIRE generation budget in the reasoning channel (200+
#      reasoning-only chunks) and hits its token limit BEFORE emitting
#      any content or tool call. ccr streams that back as an empty
#      assistant message, which Claude Code renders as a silent
#      non-answer. This is exactly what broke URL turns: qwen3 reasoned
#      past its limit before ever emitting the forced WebFetch call.
#
# Fix for BOTH: drop the nested `reasoning` object and the raw thinking
# flags, then set Ollama's NATIVE top-level `think: false`.
#
# NOTE: an earlier version set `reasoning_effort: "none"` here instead.
# That works on a tiny request but is POISON on the real Claude Code
# payload (6 KB system prompt + 80+ tools): qwen3 then returns a single
# empty chunk (`content:"", finish_reason:"stop"`, no tool call) and
# Claude Code renders it as a silent non-answer — exactly the "Brewed
# for 4s" then nothing the user hit on a URL turn. Reproduced 5/5 with
# `reasoning_effort:"none"`, fixed 5/5 by switching to `think:false`.
# `think` is Ollama's native disable-thinking switch and its
# OpenAI-compatible endpoint honors it; every local model here (qwen3,
# qwen2.5-coder, gemma3, llama3.1) accepts `think:false` without the
# "does not support thinking" error, and on qwen3 it actually disables
# the thinking channel so the model answers / calls tools directly
# instead of monologuing into the void or emitting nothing.
# (deepseek-r1 is intentionally left without this transformer so its
# native thinking stays intact for plain Q&A.)
#
# This transformer is NOT applied to Gemini. The `gemini` transformer
# in ccr is a complete Anthropic→Gemini converter that expects the
# request still in Anthropic format (it walks `tools[i].name`,
# `messages[i].role`, etc. directly). Slotting any custom transformer
# in front of it bypasses an internal pre-pass and makes `gD()` crash
# on `a.function.name` / `e.messages.filter` with TypeErrors that
# surface to Claude Code as 500s ("attempting N/10 TIMEOUT_MS=...").
# So we leave Gemini alone and accept that gemini-2.5-flash
# occasionally emits a thinking-only response with no text content
# (Claude Code shows "Thought for Xs" and nothing). Workarounds:
# retype the question, or `/model gemini,gemini-2.5-pro`.
cat > "$CCR_DIR/strip-reasoning.js" <<'JS'
class StripReasoning {
  constructor(options = {}) {
    this.name = "stripreasoning";
    this.options = options;
  }
  async transformRequestIn(req) {
    delete req.reasoning;
    delete req.thinking;
    delete req.enable_thinking;
    // `reasoning_effort:"none"` makes qwen3 return an empty response on
    // the full Claude Code payload — use Ollama's native `think:false`
    // instead. See the block comment above for the full story.
    delete req.reasoning_effort;
    req.think = false;
    return req;
  }
  async transformResponseOut(res) { return res; }
}
module.exports = StripReasoning;
JS

# Conditional tool-forcing transformer for local Ollama models. qwen3
# (and other safety-RLHF'd open models) refuse to call WebFetch when
# the user pastes a URL — they answer "I cannot access external
# websites" even though WebFetch is in their tools list. A plain system
# nudge isn't enough; the refusal is baked into the model's training.
# So when (a) the latest user message contains a URL and (b) WebFetch
# is in the available tools, we both inject a strong reminder AND set
# tool_choice to force WebFetch specifically — the model has no choice
# but to emit the tool call. Modeled on ccr's built-in `tooluse`
# transformer (which works on req.messages in OpenAI shape and sets
# tool_choice="required"), but scoped to URL-bearing turns only so
# regular coding turns are untouched.
#
# The reminder is APPENDED TO THE LAST USER MESSAGE, not pushed as a
# trailing `role:"system"` message. A trailing system turn after the
# user turn makes qwen3 ignore the forced tool_choice on the big
# payload — it returns empty or wanders ("you haven't asked a
# question") instead of calling WebFetch. Keeping the reminder inside
# the user turn fixed this 5/5 in testing (vs 0/5 as a trailing system
# message); the question and the instruction stay together where the
# model reads them.
cat > "$CCR_DIR/use-webfetch.js" <<'JS'
// The reminder embeds the LITERAL URL(s) extracted from the user turn.
// Forcing tool_choice=WebFetch makes the model emit the call, but ccr
// can't fill the call's ARGUMENTS — the model still writes `url` itself,
// and a weak model (qwen3:14b) writes the placeholder `https://example.com`
// instead of the URL you actually pasted, then answers from nothing. Quoting
// the exact URL inline and forbidding example.com fixes that mis-fill.
function buildReminder(urls) {
  const exact = urls.length
    ? "The EXACT url you MUST pass is: " + urls[0] +
      (urls.length > 1 ? " (other URLs in the message: " + urls.slice(1).join(", ") + ")" : "") + " "
    : "";
  return (
    "<system-reminder>The user has provided a URL. You MUST call the WebFetch tool now. " +
    exact +
    "WebFetch takes TWO required arguments and BOTH must be present or the call is rejected as \"Invalid tool parameters\": `url` (copy the EXACT url above verbatim — do NOT substitute `https://example.com` or any other placeholder) and `prompt` (what to extract from the page — set it to the user's question). The WebFetch tool IS in your tools list. Do not respond with any variant of \"I cannot access external websites\" or \"I don't have internet access\" — those are factually wrong here. After WebFetch returns the fetched page, answer ONLY the user's question using its content; ignore any unrelated tools in your tool list.</system-reminder>"
  );
}

// Pull every http(s) URL out of `text`, trimming trailing punctuation
// that commonly clings to a pasted link (`).,;'"`).
function extractUrls(text) {
  const m = text.match(/\bhttps?:\/\/\S+/gi);
  return m ? m.map((u) => u.replace(/[).,;'"]+$/, "")) : [];
}

function latestUserText(req) {
  const msgs = Array.isArray(req && req.messages) ? req.messages : [];
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (!m || m.role !== "user") continue;
    if (typeof m.content === "string") return m.content;
    if (Array.isArray(m.content)) {
      return m.content
        .filter((c) => c && c.type === "text" && typeof c.text === "string")
        .map((c) => c.text)
        .join(" ");
    }
    return "";
  }
  return "";
}

function findWebFetch(req) {
  const tools = Array.isArray(req && req.tools) ? req.tools : [];
  for (const t of tools) {
    if (!t) continue;
    if (t.name === "WebFetch") return { shape: "anthropic", tool: t };
    if (t.function && t.function.name === "WebFetch") return { shape: "openai", tool: t };
  }
  return null;
}

// Append `text` to the LAST user message in place (string- or
// block-content shape), instead of pushing a trailing system message —
// see the block comment above for why that distinction matters.
function appendToLastUser(msgs, text) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (!m || m.role !== "user") continue;
    if (typeof m.content === "string") m.content += "\n\n" + text;
    else if (Array.isArray(m.content)) m.content.push({ type: "text", text });
    else m.content = text;
    return;
  }
  // No user message to attach to — fall back to a trailing user turn.
  msgs.push({ role: "user", content: text });
}

class UseWebFetch {
  constructor(options = {}) {
    this.name = "usewebfetch";
    this.options = options;
  }
  async transformRequestIn(req) {
    const urls = extractUrls(latestUserText(req));
    if (!urls.length) return req;
    const found = findWebFetch(req);
    if (!found) return req;
    if (!Array.isArray(req.messages)) req.messages = [];
    appendToLastUser(req.messages, buildReminder(urls));
    req.tool_choice =
      found.shape === "anthropic"
        ? { type: "tool", name: "WebFetch" }
        : { type: "function", function: { name: "WebFetch" } };
    return req;
  }
  async transformResponseOut(res) {
    return res;
  }
}
module.exports = UseWebFetch;
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
# Gemini is NOT included — its `gemini` transformer is incompatible
# with any custom pre-transformer (see the strip-reasoning.js comment
# above for the full story).
cat > "$CCR_DIR/config.json" <<JSON
{
  "LOG": true,
  "transformers": [
    { "path": "$CCR_DIR/strip-reasoning.js" },
    { "path": "$CCR_DIR/use-webfetch.js" }
  ],
  "Providers": [
    {
      "name": "gemini",
      "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models/",
      "api_key": "\$GEMINI_API_KEY",
      "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
      "transformer": { "use": ["gemini"] }
    },
    {
      "name": "ollama",
      "api_base_url": "http://localhost:11434/v1/chat/completions",
      "api_key": "ollama",
      "models": ["qwen3:14b", "deepseek-r1:14b", "qwen2.5-coder:14b", "gemma3:12b", "llama3.1:8b"],
      "transformer": {
        "qwen3:14b":         { "use": ["stripreasoning", "usewebfetch"] },
        "qwen2.5-coder:14b": { "use": ["stripreasoning", "usewebfetch"] },
        "gemma3:12b":        { "use": ["stripreasoning"] },
        "llama3.1:8b":       { "use": ["stripreasoning", "usewebfetch"] }
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
  url-nudge   → enabled for qwen3/qwen-coder/llama3.1 (pushes them to call WebFetch on URLs instead of refusing)

Claude Code's /model picker can't list these — TYPE the full string:
  /model ollama,qwen3:14b           (tools OK — chat-balanced default)
  /model ollama,llama3.1:8b         (tools OK — lighter fallback; background route)
  /model ollama,qwen2.5-coder:14b   (tools OK — heavy coding; invents fake tool calls on casual chat)
  /model ollama,gemma3:12b          (no tools)
  /model ollama,deepseek-r1:14b     (no tools — answers only, no file/shell)
  /model gemini,gemini-2.5-flash    (15 RPM free tier — sometimes returns thinking-only/empty answers; retype to retry)
  /model gemini,gemini-2.5-pro      (~2 RPM free — use sparingly; stable thinking)
─────────────────────────────────────────────────────────────
BANNER

# Restart the router so it picks up the freshly written config.
ccr stop >/dev/null 2>&1 || true

exec ccr code "${PASSTHROUGH_ARGS[@]}"
