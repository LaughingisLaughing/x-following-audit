#!/usr/bin/env python3
"""
Answer xfa review batches through the SuperGrok login that a local Hermes Agent manages
(xAI Responses API, no tools). Only the OAuth credential is accepted, never a billed XAI_API_KEY.
Run with Hermes' own interpreter from ~/.hermes/hermes-agent; xfa.py judge --hermes does that.

On a Grok Bot cloud computer this script is not needed: the Bot reads each batch's .md itself
and writes the .jsonl answer.

usage: grok_review_bridge.py jobs.json out.jsonl [workers] [model]
  jobs.json  [{"id": "b001", "prompt": "..."}]
  out.jsonl  one line per job; jobs already answered ok are skipped, so a rerun resumes
"""

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERMES_DIR = os.path.expanduser(os.environ.get("HERMES_AGENT_DIR", "~/.hermes/hermes-agent"))
sys.path.insert(0, HERMES_DIR)

import requests  # noqa: E402
import tools.x_search_tool as xst  # noqa: E402
from tools import xai_http  # noqa: E402

CRED_LOCK = threading.Lock()   # xAI rotates refresh tokens: never let two threads refresh at once
WRITE_LOCK = threading.Lock()
STOP = threading.Event()       # set on 402/403: the allowance is used up, start nothing more
EFFORT = os.environ.get("XFA_GROK_EFFORT", "medium")


def creds(force=False, hint=None):
    with CRED_LOCK:
        c = xai_http.resolve_xai_http_credentials(prefer_api_key=False, force_refresh=force, api_key_hint=hint)
    if c.get("provider") != "xai-oauth" or not c.get("api_key"):
        raise RuntimeError(f"no SuperGrok OAuth credential (got {c.get('provider')!r}); not using a billed key")
    return c["api_key"], (c.get("base_url") or "https://api.x.ai/v1").rstrip("/")


def call(job, model):
    if STOP.is_set():
        return {"id": job["id"], "ok": False, "error": "not started: usage allowance used up", "skipped": True}
    payload = {"model": model, "input": [{"role": "user", "content": job["prompt"]}], "store": False,
               "reasoning": {"effort": EFFORT}}
    t0, err = time.time(), "gave up after retries"
    key, base = creds()
    for attempt in range(4):
        if STOP.is_set():   # another job hit the limit while this one waited or backed off
            return {"id": job["id"], "ok": False, "error": "not sent: usage allowance used up", "skipped": True}
        try:
            r = requests.post(f"{base}/responses", json=payload, timeout=300, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                "User-Agent": xai_http.hermes_xai_user_agent()})
        except (requests.ReadTimeout, requests.ConnectionError) as e:
            err = type(e).__name__
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 401 and attempt == 0:
            key, base = creds(force=True, hint=key)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            err = f"HTTP {r.status_code}"
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code >= 400:   # 403 spending-limit means the weekly allowance is used up: stop, do not retry
            if r.status_code in (402, 403):
                STOP.set()
            return {"id": job["id"], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        data = r.json()
        return {"id": job["id"], "ok": True, "answer": xst._extract_response_text(data),
                "usage": data.get("usage"), "secs": round(time.time() - t0, 1)}
    return {"id": job["id"], "ok": False, "error": err}


def main():
    jobs = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    model = sys.argv[4] if len(sys.argv) > 4 else "grok-4.3"
    done = set()
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ok"):
                done.add(r["id"])
    todo = [j for j in jobs if j["id"] not in done]
    print(f"{len(done)} done, {len(todo)} to run; model {model}, effort {EFFORT}, {workers} workers",
          file=sys.stderr, flush=True)
    creds()   # resolve, and refresh if due, once before the threads start
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(call, j, model) for j in todo]
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as e:   # report and let the other jobs carry on
                r = {"id": "?", "ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
            with WRITE_LOCK, open(out, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            u = r.get("usage") or {}
            print(time.strftime("%H:%M:%S"), r["id"], "ok" if r.get("ok") else "FAIL " + str(r.get("error"))[:160],
                  "tokens", u.get("total_tokens"), "secs", r.get("secs"), file=sys.stderr, flush=True)
    if STOP.is_set():
        sys.exit(3)   # xfa.py judge --hermes tells the user the allowance ran out


if __name__ == "__main__":
    main()
