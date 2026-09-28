"""Live study: run each engine against a real page in a real browser.

All engines start from the same page state, loaded from the same URL on a
local copy of orders_app.html (so nothing external can change under the
test):

  jev, haiku, opus   Our Playwright code scrapes the page with
                     get_interactive_elements() (same scraper and "grand"
                     context as the offline studies), the engine picks one
                     element or "none", and our code performs the task's
                     action (click / select) on that element.
  agent              Claude (AGENT_MODEL, default claude-opus-5-5) drives the
                     browser itself through the real playwright-mcp server:
                     it takes its own snapshots, decides, and acts. It may
                     reply CANNOT_FIND instead of acting.

After each task the page's own event log (window.__events) says what
actually happened, and the outcome is scored from that, not from the
engine's answer:

  correct        the expected change happened and nothing else did
  wrong-action   some other state change happened (wrong row, wrong control)
  refused        no change, and the engine said none / CANNOT_FIND
  no-op          no change and no refusal
  for tasks whose target does not exist, "refused" is the right outcome and
  any state change is a false action

  python run_live.py --dry-run
  python run_live.py --engines jev,haiku,opus --repeats 2 --max-usd 1
  python run_live.py --engines agent --repeats 2 --max-usd 5
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from typesafe_client import load_dotenv

load_dotenv()

from ambiguity_common import line  # noqa: E402
from browser_actions import act, get_interactive_elements  # noqa: E402
from engines import ABSTAIN_ENGINES, OPUS, price  # noqa: E402
from record_ambiguity import candidates_with_contexts  # noqa: E402
from live_tasks import TASKS, score  # noqa: E402,F401

PORT = 8767
REP = "grand"  # the scraper's default context, as in Study 1

AGENT_SYSTEM = (
    "You control a real browser through Playwright MCP tools and carry out one instruction on the "
    "current page. Look at the page first. If the instruction can be done, do it. If the element it "
    "needs is not there, or you cannot tell which element is meant, do not act: reply with "
    "CANNOT_FIND and a one-line reason. Do not do anything other than what the instruction asks."
)


def serve():
    import functools
    import http.server
    import threading

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(H, directory=os.path.dirname(os.path.abspath(__file__)))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{PORT}/orders_app.html"


def url_for(base, task):
    return f"{base}?role={task['role']}&page=1"


def run_picker(engine, task, base, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(url_for(base, task))
    page.wait_for_selector("#rows tr")
    scraped = candidates_with_contexts(page)
    lines = [line(e, REP) for e in scraped]
    res = ABSTAIN_ENGINES[engine](task["goal"], lines)
    acted, act_error = None, None
    if res.get("answer") not in (None, -1):
        el = scraped[res["answer"]]
        acted = {"role": el["role"], "name": el["name"], "context": el["contexts"][REP][:80]}
        try:
            act(page, el, task["action"], task.get("value"))
        except Exception as e:  # noqa: BLE001 -- e.g. selecting on a button
            act_error = f"{type(e).__name__}: {e}"[:200]
    time.sleep(0.3)
    events = page.evaluate("() => window.__events")
    page.close()
    return {"engine_result": {k: v for k, v in res.items() if k != "answer"}, "answer": res.get("answer"),
            "n_candidates": len(scraped), "acted_on": acted, "act_error": act_error, "events": events,
            "refused": res.get("answer") == -1, "cost_usd": res.get("cost_usd", 0.0),
            "latency_s": res.get("latency_s"), "error": res.get("error")}


async def run_agent_task(task, base, anthropic_client, model):
    import mcp_playwright_agent as agent
    async with agent.PlaywrightMCPClient(headed=False) as client:
        await client.call_tool("browser_navigate", {"url": url_for(base, task)})
        messages = [{"role": "user", "content": f"Instruction: {task['goal']}"}]
        tin = tout = 0
        tool_calls, final_text = [], ""
        start = time.monotonic()
        for _ in range(10):
            resp = anthropic_client.messages.create(model=model, max_tokens=1024, system=AGENT_SYSTEM,
                                                    tools=client.anthropic_tools, messages=messages)
            tin += resp.usage.input_tokens
            tout += resp.usage.output_tokens
            uses = [b for b in resp.content if b.type == "tool_use"]
            final_text = " ".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
            if not uses:
                break
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for b in uses:
                tool_calls.append({"name": b.name, "input": b.input})
                try:
                    out = await client.call_tool(b.name, b.input)
                except Exception as e:  # noqa: BLE001
                    out = f"ERROR calling {b.name}: {e}"
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out[:6000]})
            messages.append({"role": "user", "content": results})
        latency = time.monotonic() - start
        events = await client.evaluate("() => JSON.stringify(window.__events)")
        if isinstance(events, str):
            try:
                events = json.loads(events)
            except json.JSONDecodeError:
                events = []
    cost = price(model, {"input_tokens": tin, "output_tokens": tout})
    return {"answer": None, "final_text": final_text[:500], "tool_calls": tool_calls,
            "tool_call_count": len(tool_calls), "events": events or [],
            "refused": "CANNOT_FIND" in final_text, "usage": {"input_tokens": tin, "output_tokens": tout},
            "cost_usd": cost, "latency_s": latency, "error": None, "model": model}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="jev,haiku,opus,agent")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    ap.add_argument("--max-usd", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="results/live.jsonl")
    args = ap.parse_args()

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    tasks = [t for t in TASKS if not args.tasks or t["id"] in args.tasks.split(",")]
    jobs = [(e, t["id"], k) for e in engines for t in tasks for k in range(args.repeats)]
    done = set()
    if os.path.exists(args.out):
        for ln in open(args.out):
            r = json.loads(ln)
            if not r.get("error"):
                done.add((r["engine"], r["task_id"], r["repeat"]))
    todo = [j for j in jobs if j not in done]
    est = {"jev": 0.0001, "haiku": 0.002, "opus": 0.008, "agent": 0.20}
    print(f"{len(jobs)} runs, {len(done)} done, {len(todo)} to run")
    for e in engines:
        n = sum(1 for j in todo if j[0] == e)
        print(f"  {e:<6} {n:>3} runs  est. ${n * est.get(e, 0):.2f}")
    if args.dry_run:
        return
    if args.max_usd is None:
        sys.exit("refusing to run without --max-usd")

    base = serve()
    model = os.environ.get("AGENT_MODEL", OPUS)
    try:
        rev = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        rev = None
    by_id = {t["id"]: t for t in tasks}
    os.makedirs("results", exist_ok=True)
    spent = 0.0
    from playwright.sync_api import sync_playwright

    def record(f, e, task, k, res):
        outcome = None if res.get("error") else score(task, res["events"], res["refused"])
        row = {"run_id": uuid.uuid4().hex[:8], "engine": e, "task_id": task["id"], "kind": task["kind"],
               "goal": task["goal"], "repeat": k, "outcome": outcome, "git_rev": rev,
               "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **res}
        f.write(json.dumps(row) + "\n")
        f.flush()
        return outcome

    picker_jobs = [j for j in todo if j[0] != "agent"]
    agent_jobs = [j for j in todo if j[0] == "agent"]
    n = 0
    with open(args.out, "a") as f:
        # Phase 1: scrape-decide-act engines, in our own Playwright browser.
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for e, tid, k in picker_jobs:
                n += 1
                if spent >= args.max_usd:
                    print(f"budget cap reached: ${spent:.3f}")
                    break
                res = run_picker(e, by_id[tid], base, browser)
                spent += res.get("cost_usd") or 0.0
                oc = record(f, e, by_id[tid], k, res)
                print(f"[{n}/{len(todo)}] ${spent:.3f} {e:<6} {tid:<20} {oc}"
                      + (f"  ERROR {res['error'][:100]}" if res.get("error") else ""))
            browser.close()
        # Phase 2: the playwright-mcp agent, which runs its own browser.
        if agent_jobs and spent < args.max_usd:
            from anthropic import Anthropic
            anthropic_client = Anthropic()
            for e, tid, k in agent_jobs:
                n += 1
                if spent >= args.max_usd:
                    print(f"budget cap reached: ${spent:.3f}")
                    break
                try:
                    res = asyncio.run(run_agent_task(by_id[tid], base, anthropic_client, model))
                except Exception as ex:  # noqa: BLE001
                    res = {"error": f"{type(ex).__name__}: {ex}"[:300], "events": [], "refused": False,
                           "cost_usd": 0.0}
                spent += res.get("cost_usd") or 0.0
                oc = record(f, e, by_id[tid], k, res)
                print(f"[{n}/{len(todo)}] ${spent:.3f} {e:<6} {tid:<20} {oc}"
                      + (f"  ERROR {res['error'][:100]}" if res.get("error") else ""))
    print(f"spent this run: ${spent:.3f}")


if __name__ == "__main__":
    main()
