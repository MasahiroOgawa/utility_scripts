# Free Claude Code via claude-code-router

Run Claude Code against Google Gemini's free tier (or any other
OpenAI-compatible provider) without a paid Anthropic subscription.
Scoped to this repo via `script/claude_gemini.sh`; the system-wide
`claude` command is left untouched, so other repos keep using your
Anthropic account.

## Why a router is needed

Setting `ANTHROPIC_BASE_URL` directly at Gemini — the advice in most
blog posts — does **not** work alone:

- Claude Code POSTs Anthropic Messages format to `/v1/messages`.
- Gemini's `/v1beta/openai/` endpoint speaks OpenAI chat-completions.
- Gemini's native `/v1beta/models/` endpoint speaks Gemini's own format.

None of these is directly compatible. `claude-code-router` runs as a
local daemon on `127.0.0.1:3456` that translates Anthropic ↔ provider
format on the fly, so Claude Code itself stays unmodified.

## One-time setup

### 1. Get a Gemini API key

1. Open <https://aistudio.google.com/>.
2. Sign in with any Google account (no credit card required).
3. **Get API key → Create API key**. Copy the `AIza…` value.

Free-tier quota on `gemini-2.5-flash` is roughly 15 requests/min and
1500/day (subject to change). Enough for normal coding sessions; bursty
agent runs can hit the per-minute limit.

### 2. Install claude-code-router

Repo convention is `bun`, but `npm install -g …` works identically.

```bash
bun install -g @musistudio/claude-code-router
ccr --version    # 2.0.0 or newer
```

You also need Claude Code itself:

```bash
bun install -g @anthropic-ai/claude-code
```

### 3. Configure `.env`

Add to repo-root `.env` (gitignored — never commit):

```dotenv
# Gemini key for claude-code-router (free Claude Code).
GEMINI_API_KEY=AIza...

# Kokuuz server reads this name; alias so .env stays single-source.
ANTHROPIC_API_KEY="$GEMINI_API_KEY"
```

Order matters — `GEMINI_API_KEY` must come **before** the line that
references it. The `set -a && source .env && set +a` loader used by
both `script/claude_gemini.sh` and `script/start_server.sh` expands the
`$GEMINI_API_KEY` reference at source time.

### 4. Launch

From the repo root:

```bash
./script/claude_gemini.sh
```

That sources `.env`, regenerates `~/.claude-code-router/config.json`
with the Gemini provider, restarts the router daemon, and `exec`s
`ccr code` (which launches Claude Code pointed at the router).

To switch models on the fly, **type** the command — don't use the
arrow-key picker:

```
/model gemini,gemini-2.5-pro
/model gemini,gemini-2.5-flash
```

The picker UI is baked into Claude Code and only lists Anthropic
models; it doesn't know about routed providers. The typed
`provider,model` form is intercepted by the router daemon.

The wrapper writes the config with `"api_key": "$GEMINI_API_KEY"`
literally — the router resolves the variable at config-load time from
the env we exported, so the API key never lands on disk in plaintext.

## How it works

```
you → ./script/claude_gemini.sh
       ├─ sources .env  (GEMINI_API_KEY into env)
       ├─ writes ~/.claude-code-router/config.json
       ├─ ccr stop && ccr code
       ▼
   ccr daemon on 127.0.0.1:3456  (Anthropic-format in)
       │ translates /v1/messages ⇄ Gemini native
       ▼
   https://generativelanguage.googleapis.com/v1beta/models/
```

`ccr code` exports `ANTHROPIC_BASE_URL=http://127.0.0.1:3456` and
launches the regular `claude` binary, which talks to the router as if
it were Anthropic.

## Using a different free LLM

`script/claude_gemini.sh` hard-codes a Gemini provider block. To use a
different backend, edit the heredoc in the wrapper (or maintain
`~/.claude-code-router/config.json` by hand). The router ships
transformers for many providers and supports `"$ENV_VAR"` substitution
in config values, so secrets stay in `.env`.

- **OpenRouter** — mixed free / paid models. Key from
  <https://openrouter.ai>.

  ```json
  {
    "name": "openrouter",
    "api_base_url": "https://openrouter.ai/api/v1/chat/completions",
    "api_key": "$OPENROUTER_API_KEY",
    "models": [
      "google/gemini-2.5-flash",
      "deepseek/deepseek-chat",
      "meta-llama/llama-3.3-70b-instruct:free"
    ],
    "transformer": { "use": ["openrouter"] }
  }
  ```

- **Ollama** — fully local, no key needed. Same backend Kokuuz uses for
  the summariser when `llm.provider: local`.

  ```json
  {
    "name": "ollama",
    "api_base_url": "http://localhost:11434/v1/chat/completions",
    "api_key": "ollama",
    "models": ["deepseek-r1:14b", "qwen3:14b"]
  }
  ```

- **DeepSeek**, **Volcengine**, **SiliconFlow** — see the
  `claude-code-router` README for transformer names and base URLs.

Set `Router.default` to `"<provider-name>,<model-id>"` (always the
two-part form — `"gemini-2.5-flash"` alone silently fails to route)
to pick which combination is used when Claude Code starts. Other
slots (`background`, `think`, `longContext`, `webSearch`) follow the
same format and let you split roles across providers.

## Troubleshooting

- **`ccr: command not found`** — `bun`'s global bin (`~/.bun/bin`) isn't
  on `PATH`. Add `export PATH="$HOME/.bun/bin:$PATH"` to your shell rc.

- **Wrapper errors with `ANTHROPIC_API_KEY not set`** — either `.env`
  is missing at the repo root, or `GEMINI_API_KEY` is defined *after*
  the alias line.

- **`ccr status` says Running but Claude Code returns 401** — the
  Gemini key is rejected. Smoke-test it directly:

  ```bash
  curl -sS -H "Content-Type: application/json" \
    -H "Authorization: Bearer $GEMINI_API_KEY" \
    -d '{"model":"gemini-2.5-flash","messages":[{"role":"user","content":"ping"}]}' \
    https://generativelanguage.googleapis.com/v1beta/openai/chat/completions | head
  ```

- **Verify the router itself** without launching Claude — send an
  Anthropic-format request straight to it:

  ```bash
  ccr start
  curl -sS -X POST http://127.0.0.1:3456/v1/messages \
    -H "Content-Type: application/json" \
    -H "anthropic-version: 2023-06-01" \
    -d '{"model":"gemini-2.5-flash","max_tokens":32,
         "messages":[{"role":"user","content":"Say: pong"}]}'
  ccr stop
  ```

  A valid response looks like
  `{"type":"message",…,"content":[{"type":"text","text":"pong"}], …}`.

- **HTTP 429 / rate limit** — free Gemini tier is ~15 req/min. Pace
  agentic workflows, or pay for a higher tier (still well below Claude
  pricing for most usage).

- **Router log** lives at `~/.claude-code-router/claude-code-router.log`
  when `"LOG": true` is set in `config.json` (the wrapper sets it to
  `false` for cleanliness; flip it when debugging).

## Caveats

- **Prompt caching** — Anthropic's 5-minute cache doesn't exist on
  Gemini. Long sessions cost more tokens than they would on the
  Anthropic backend.
- **Tool-use schemas** subtly differ. Most Claude Code skills, agents,
  and slash commands work, but a few (especially ones leaning on
  Anthropic-specific structured outputs or extended thinking) may
  misbehave.
- **Model identity** — Claude Code believes it is talking to a Claude
  model. Asking it "what model are you?" returns Claude branding even
  though Gemini is generating the reply.
- **Global `claude` is unchanged.** Other repos still use your
  Anthropic account. To make Gemini the default everywhere, alias
  `claude='ccr code'` in your shell rc — but that's out of scope here.
