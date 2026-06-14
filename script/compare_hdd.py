#!/usr/bin/env python3
"""Harder open-web-search task: cheapest buyable NEW 4TB external HDD (JP market).

Unlike the SET_B questions in compare_models.py (which hand the model a URL to
fetch), this task needs live web SEARCH to *find* a product. That separates the
agentic web-capable models (claude, gemini-cli) from ones that can't search
(aider has no web search; the router tool-loops are flaky).

Flow: run the same 5 contestants -> save every raw answer -> one Claude
comparative judge extracts (product, price_jpy, url, validity) per model and
names the cheapest valid one. URLs are verified by hand afterwards.

Reuses helpers from compare_models.py. RUN_ONLY="a,b" re-runs a subset; other
rows load from the previous run's saved bench_out/<model>__hdd.txt files.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_models as cm  # noqa: E402

WEB_TIMEOUT = 300

Q = (
    "Find the CHEAPEST currently in-stock, buyable, NEW (not used or refurbished) "
    "4TB external hard disk (HDD) on the JAPANESE market right now. Search Japanese "
    "retailers such as Amazon.co.jp, kakaku.com, NTT-X Store, or Bic Camera. "
    "Report exactly three things, concisely:\n"
    "1) product name\n"
    "2) price in JPY (税込)\n"
    "3) the exact product-page URL where it can be purchased.\n"
    "Give the single cheapest option you can actually buy now."
)

CONTESTANTS = cm.CONTESTANTS


def ask_claude_web(prompt):
    return cm.run(["claude", "-p", prompt, "--allowedTools", "WebSearch", "WebFetch"],
                  env=cm.clean_env(), timeout=WEB_TIMEOUT)


def ask_ccr_web(prompt):
    return cm.run(["ccr", "code", "-p", prompt, "--allowedTools", "WebSearch", "WebFetch"],
                  env=cm.clean_env(), timeout=WEB_TIMEOUT)


def ask_gemini_web(prompt):
    return cm.run(["gemini", "-m", "gemini-2.5-flash", "-p", prompt],
                  env=cm.gemini_env(), timeout=WEB_TIMEOUT)


def ask_aider(prompt):
    workdir = cm.OUT / "aider_tmp" / "hdd"
    workdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["OLLAMA_API_BASE"] = cm.OLLAMA_BASE
    cmd = [
        "aider", "--model", "ollama/qwen3:14b", "--message", prompt,
        "--no-auto-commits", "--yes-always", "--no-gitignore",
        "--no-check-update", "--map-tokens", "0", "--no-pretty",
    ]
    return cm.run(cmd, env=env, cwd=str(workdir), timeout=WEB_TIMEOUT)


def main():
    cm.OUT.mkdir(exist_ok=True)
    answers = {}
    only = os.environ.get("RUN_ONLY", "").strip()
    run_set = set(c.strip() for c in only.split(",") if c.strip()) if only else set(CONTESTANTS)
    cm.log(f"running contestants: {sorted(run_set)} (others loaded from disk)")

    def rec(c, ans, dt):
        answers[c] = ans
        (cm.OUT / f"{c}__hdd.txt").write_text(ans)
        cm.log(f"  {c}: {dt:5.1f}s  {len(ans)}c")

    if "router-gemini" in run_set:
        cm.bootstrap_router("--cloud")
        cm.log("router-gemini (ccr agent + web search)")
        a, dt = ask_ccr_web(Q)
        rec("router-gemini", a, dt)
    if "router-qwen3" in run_set:
        cm.bootstrap_router("--local")
        cm.log("router-qwen3 (ccr agent + web search)")
        a, dt = ask_ccr_web(Q)
        rec("router-qwen3", a, dt)
    if "claude" in run_set:
        cm.log("claude (WebSearch)")
        a, dt = ask_claude_web(Q)
        rec("claude", a, dt)
    if "gemini-cli" in run_set:
        cm.log("gemini-cli (built-in search)")
        a, dt = ask_gemini_web(Q)
        rec("gemini-cli", a, dt)
    if "aider-qwen3" in run_set:
        cm.log("aider-qwen3 (no web search -- expected weak)")
        a, dt = ask_aider(Q)
        rec("aider-qwen3", a, dt)

    cm.run(["ccr", "stop"], timeout=20)

    for c in CONTESTANTS:
        if c not in answers:
            f = cm.OUT / f"{c}__hdd.txt"
            answers[c] = f.read_text() if f.exists() else "[NOT RUN]"

    # Comparative Claude judge: extract price/url/validity per model, pick cheapest.
    blocks = "\n\n".join(f"=== {c} ===\n{answers[c][:2500]}" for c in CONTESTANTS)
    judge_prompt = (
        "Below are answers from several models to: find the CHEAPEST currently-buyable "
        "NEW (not used/refurbished) 4TB external hard disk on the JAPANESE market, with a "
        "product-page URL and price in JPY.\n\n"
        "Output ONLY a JSON array with one object per model, in THIS exact order: "
        + ", ".join(CONTESTANTS) + ". Each object: "
        '{"model": <name>, "product": <name or null>, "price_jpy": <integer yen or null>, '
        '"url": <product url or null>, '
        '"valid": <true ONLY if it names a NEW 4TB external HDD with a concrete product URL and a price>, '
        '"notes": <short reason>}. '
        "After the JSON array, add one final line exactly: "
        "CHEAPEST=<model with the lowest price_jpy among valid ones, or none>.\n\n"
        + blocks
    )
    out, _ = cm.run(["claude", "-p", judge_prompt], env=cm.clean_env(), timeout=180)
    (cm.OUT / "hdd_judge.txt").write_text(out)
    print("\n===== CLAUDE COMPARATIVE JUDGE =====\n" + out + "\n")
    cm.log("raw answers in bench_out/<model>__hdd.txt ; judge in bench_out/hdd_judge.txt")


if __name__ == "__main__":
    sys.exit(main())
