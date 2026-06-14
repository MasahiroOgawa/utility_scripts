#!/usr/bin/env python3
"""Quick accuracy bake-off across 5 LLM CLI backends (< ~30 min).

Contestants:
  router-gemini  claude_router.sh -> ccr -> gemini,gemini-2.5-flash
  router-qwen3   claude_router.sh -> ccr -> ollama,qwen3:14b (think:false, by router design)
  claude         real Claude via plain `claude -p` (OAuth) -- reference ceiling
  gemini-cli     `gemini -p`
  aider-qwen3    `aider --model ollama/qwen3:14b` (direct ollama, thinking ON)

Question sets:
  Set A  deterministic, no tools  -> keyword/substring grading
  Set B  needs to fetch a URL     -> Claude-judge grading (0/1)

Router backends:
  Set A -> raw POST to the ccr endpoint with explicit "provider,model".
  Set B -> the real claude agent through ccr (WebFetch available; the router's
           use-webfetch.js transformer pushes qwen3 to actually fetch). The ccr
           daemon's default route is switched between contestants so each Set B
           call lands on the intended model.

Raw outputs are saved under bench_out/ for spot-checking.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTER = REPO / "script" / "claude_router.sh"
OUT = REPO / "bench_out"
CCR_URL = "http://127.0.0.1:3456/v1/messages"
PER_CALL_TIMEOUT = 240          # seconds per single model call
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ---- ground truth -----------------------------------------------------------
AWARD_URL = "https://mot-innovation-award.com/"
RFC_URL = "https://www.rfc-editor.org/rfc/rfc2324.txt"   # HTCPCP, stable text

SET_A = [
    {"id": "math",     "q": "What is 17 * 23? Reply with ONLY the number.",
     "keywords": ["391"]},
    {"id": "capital",  "q": "What is the capital city of Australia? Reply with ONLY the city name.",
     "keywords": ["canberra"]},
    {"id": "pysum",    "q": "In Python, what integer does sum(range(1, 101)) evaluate to? Reply with ONLY the number.",
     "keywords": ["5050"]},
]

SET_B = [
    {"id": "award",
     "q": (f"Read this page: {AWARD_URL} . "
           "For 第13回技術経営・イノベーション大賞, how much prize money (賞金) "
           "does the first-prize winner (内閣総理大臣賞) receive? Answer with the amount."),
     "ground_truth": "The first prize (内閣総理大臣賞) is 50万円 (500,000 yen)."},
    {"id": "rfc",
     "q": (f"Read this page: {RFC_URL} . "
           "What is the title of this RFC and which HTTP error code does it define "
           "for a teapot? Answer briefly."),
     "ground_truth": ('RFC 2324 "Hyper Text Coffee Pot Control Protocol (HTCPCP/1.0)"; '
                      'it defines error code 418 "I\'m a teapot".')},
]

CONTESTANTS = ["router-gemini", "router-qwen3", "claude", "gemini-cli", "aider-qwen3"]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd, timeout=PER_CALL_TIMEOUT, env=None, cwd=None):
    """Run a command, return (stdout+stderr text, elapsed_seconds)."""
    t0 = time.time()
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, env=env, cwd=cwd,
        )
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
        return out.strip(), time.time() - t0
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]", time.time() - t0
    except Exception as e:  # noqa: BLE001
        return f"[ERROR: {e}]", time.time() - t0


def clean_env():
    """Env with Anthropic vars stripped so `claude` uses its OAuth login."""
    e = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
        e.pop(k, None)
    return e


# ---- ccr daemon control -----------------------------------------------------
def bootstrap_router(mode):
    """Run claude_router.sh in --cloud/--local with a throwaway prompt.

    This writes ~/.claude-code-router/config.json (with GEMINI_API_KEY in the
    daemon env) and leaves the ccr daemon running. `mode` sets the default route
    used by Set B `ccr code` calls.
    """
    log(f"bootstrapping ccr ({mode}) ...")
    run(["bash", str(ROUTER), mode, "-p", "ok"], timeout=150)
    status, _ = run(["ccr", "status"], timeout=20)
    log(f"ccr status: {status.splitlines()[0] if status else '(no output)'}")


def ccr_curl(model, prompt):
    payload = json.dumps({
        "model": model, "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    })
    out, dt = run([
        "curl", "-sS", "-m", str(PER_CALL_TIMEOUT), CCR_URL,
        "-H", "Content-Type: application/json",
        "-H", "anthropic-version: 2023-06-01",
        "-d", payload,
    ])
    return parse_anthropic_text(out), dt


def parse_anthropic_text(raw):
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw
    if isinstance(data, dict):
        if "content" in data and isinstance(data["content"], list):
            parts = [b.get("text", "") for b in data["content"] if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(p for p in parts if p).strip() or raw
        if "error" in data:
            return f"[API error] {json.dumps(data['error'])[:400]}"
    return raw


# ---- per-contestant invocation ---------------------------------------------
def ask_claude_oauth(prompt, set_b):
    # The prompt must come right after -p; trailing flags after it are not
    # treated as the positional prompt.
    cmd = ["claude", "-p", prompt]
    if set_b:
        cmd += ["--allowedTools", "WebFetch"]
    return run(cmd, env=clean_env())


def ask_ccr_agent(prompt):
    """Set B router calls: drive the claude agent via `ccr code` (default route)."""
    return run(["ccr", "code", "-p", prompt, "--allowedTools", "WebFetch"], env=clean_env())


def gemini_env():
    """gemini-cli env with GEMINI_API_KEY taken from the repo .env (single source)."""
    e = dict(os.environ)
    key = None
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and len(line) > len("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if key:
        e["GEMINI_API_KEY"] = key
    return e


def ask_gemini_cli(prompt):
    # Pin to gemini-2.5-flash (higher free-tier RPM) so it can actually answer
    # and matches the model router-gemini uses -> apples-to-apples.
    return run(["gemini", "-m", "gemini-2.5-flash", "-p", prompt], env=gemini_env())


def ask_aider(prompt, qid):
    workdir = OUT / "aider_tmp" / qid
    workdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["OLLAMA_API_BASE"] = OLLAMA_BASE
    cmd = [
        "aider", "--model", "ollama/qwen3:14b", "--message", prompt,
        "--no-auto-commits", "--yes-always", "--no-gitignore",
        "--no-check-update", "--map-tokens", "0", "--no-pretty",
    ]
    return run(cmd, env=env, cwd=str(workdir))


# ---- grading ----------------------------------------------------------------
def grade_keyword(answer, keywords):
    low = answer.lower()
    return 1 if all(k.lower() in low for k in keywords) else 0


def grade_judge(question, ground_truth, answer):
    if answer in ("[TIMEOUT]", "") or answer.startswith("[ERROR") or answer.startswith("[API error"):
        return 0, "no usable answer"
    judge_prompt = (
        "You are grading an answer. Reply with ONLY compact JSON "
        '{"score":0 or 1,"why":"<short>"}. score=1 only if the answer is factually '
        "correct given the ground truth (the key fact must be present and correct).\n\n"
        f"QUESTION: {question}\nGROUND TRUTH: {ground_truth}\n"
        f"ANSWER TO GRADE:\n{answer[:3000]}"
    )
    out, _ = run(["claude", "-p", judge_prompt], env=clean_env(), timeout=120)
    m = re.search(r"\{.*\}", out, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            return int(d.get("score", 0)), str(d.get("why", ""))[:120]
        except Exception:  # noqa: BLE001
            pass
    return 0, f"unparseable judge output: {out[:80]}"


# ---- main -------------------------------------------------------------------
def main():
    OUT.mkdir(exist_ok=True)
    raw = {}        # (contestant, qid) -> answer
    timing = {}     # (contestant, qid) -> seconds
    t_start = time.time()

    def record(contestant, qid, ans, dt):
        raw[(contestant, qid)] = ans
        timing[(contestant, qid)] = dt
        (OUT / f"{contestant}__{qid}.txt").write_text(ans)
        flag = "TIMEOUT" if ans == "[TIMEOUT]" else f"{len(ans)}c"
        log(f"  {contestant} / {qid}: {dt:5.1f}s  {flag}")

    # RUN_ONLY="router-gemini,gemini-cli" re-runs just those; other rows are
    # loaded from the previous run's saved outputs in bench_out/.
    only = os.environ.get("RUN_ONLY", "").strip()
    run_set = set(c.strip() for c in only.split(",") if c.strip()) if only else set(CONTESTANTS)
    log(f"running contestants: {sorted(run_set)} (others loaded from disk)")
    need_cloud = "router-gemini" in run_set
    need_local = "router-qwen3" in run_set

    # Phase 1: daemon default = gemini -> router-gemini Set A+B, router-qwen3 Set A
    if need_cloud or need_local:
        bootstrap_router("--cloud")
        log("Set A: router backends via ccr curl")
        for item in SET_A:
            if need_cloud:
                a, dt = ccr_curl("gemini,gemini-2.5-flash", item["q"])
                record("router-gemini", item["id"], a, dt)
            if need_local:
                a, dt = ccr_curl("ollama,qwen3:14b", item["q"])
                record("router-qwen3", item["id"], a, dt)
    if need_cloud:
        log("Set B: router-gemini via ccr agent (default=gemini)")
        for item in SET_B:
            a, dt = ask_ccr_agent(item["q"])
            record("router-gemini", item["id"], a, dt)

    # Phase 2: daemon default = qwen3 -> router-qwen3 Set B
    if need_local:
        bootstrap_router("--local")
        log("Set B: router-qwen3 via ccr agent (default=qwen3)")
        for item in SET_B:
            a, dt = ask_ccr_agent(item["q"])
            record("router-qwen3", item["id"], a, dt)

    # Phase 3: non-router contestants (no daemon needed)
    if "claude" in run_set:
        log("claude (real, OAuth)")
        for item in SET_A:
            a, dt = ask_claude_oauth(item["q"], set_b=False)
            record("claude", item["id"], a, dt)
        for item in SET_B:
            a, dt = ask_claude_oauth(item["q"], set_b=True)
            record("claude", item["id"], a, dt)

    if "gemini-cli" in run_set:
        log("gemini-cli")
        for item in SET_A + SET_B:
            a, dt = ask_gemini_cli(item["q"])
            record("gemini-cli", item["id"], a, dt)

    if "aider-qwen3" in run_set:
        log("aider-qwen3 (thinking ON, direct ollama) -- slow path")
        for item in SET_A + SET_B:
            a, dt = ask_aider(item["q"], item["id"])
            record("aider-qwen3", item["id"], a, dt)

    run(["ccr", "stop"], timeout=20)

    # Fill rows not re-run this pass from the previous run's saved outputs.
    prior_time = {}
    tfile = OUT / "timing.json"
    if tfile.exists():
        try:
            prior_time = {tuple(k.split("||")): v for k, v in json.loads(tfile.read_text()).items()}
        except Exception:  # noqa: BLE001
            prior_time = {}
    qids_all = [i["id"] for i in SET_A] + [i["id"] for i in SET_B]
    for c in CONTESTANTS:
        for q in qids_all:
            if (c, q) in raw:
                continue
            f = OUT / f"{c}__{q}.txt"
            raw[(c, q)] = f.read_text() if f.exists() else "[MISSING]"
            timing[(c, q)] = prior_time.get((c, q), 0.0)
    tfile.write_text(json.dumps({f"{c}||{q}": timing[(c, q)] for c in CONTESTANTS for q in qids_all}))

    # ---- grade ----
    log("grading ...")
    scores = {}     # (contestant, qid) -> (score, note)
    for c in CONTESTANTS:
        for item in SET_A:
            scores[(c, item["id"])] = (grade_keyword(raw[(c, item["id"])], item["keywords"]), "")
        for item in SET_B:
            s, why = grade_judge(item["q"], item["ground_truth"], raw[(c, item["id"])])
            scores[(c, item["id"])] = (s, why)
            log(f"  judge {c}/{item['id']}: {s} ({why})")

    # ---- report ----
    qids = [i["id"] for i in SET_A] + [i["id"] for i in SET_B]
    nq = len(qids)
    lines = []
    lines.append("# Model accuracy comparison\n")
    lines.append(f"_Total wall-clock: {(time.time() - t_start) / 60:.1f} min. "
                 "Set A = keyword-graded, Set B = Claude-judged (URL fetch). "
                 "time(s)=0 means the row was carried over from a prior run (not re-run)._\n")
    header = "| contestant | " + " | ".join(qids) + " | score | acc% | time(s) |"
    sep = "|" + "---|" * (nq + 4)
    lines.append(header)
    lines.append(sep)
    for c in CONTESTANTS:
        cells = []
        total = 0.0
        ttime = 0.0
        for q in qids:
            s = scores[(c, q)][0]
            total += s
            ttime += timing[(c, q)]
            cells.append("✅" if s else "❌")
        acc = 100 * total / nq
        lines.append(f"| {c} | " + " | ".join(cells) +
                     f" | {total:.0f}/{nq} | {acc:.0f}% | {ttime:.0f} |")
    report = "\n".join(lines)
    (OUT / "RESULTS.md").write_text(report + "\n")
    print("\n" + report + "\n")
    log(f"raw outputs + RESULTS.md in {OUT}")


if __name__ == "__main__":
    sys.exit(main())
