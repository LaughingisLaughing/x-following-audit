#!/usr/bin/env python3
"""
Run Hermes' x_search tool directly (no agent loop) for xfa.py --engine grok.

Must run with Hermes' own interpreter (~/.hermes/hermes-agent/venv/bin/python3):
credentials are resolved and refreshed by Hermes' code, never read here.
Jobs run one after another in this single process so two OAuth refreshes
never race (refresh tokens rotate on xAI).

stdin : JSON array of {"handles": [...<=10], "from_date": "YYYY-MM-DD"}
stdout: one JSON line per job {"handles", "ok", "answer", "citations", "usage", "error"}
"""

import json
import os
import sys

HERMES_DIR = os.path.expanduser(os.environ.get("HERMES_AGENT_DIR", "~/.hermes/hermes-agent"))
sys.path.insert(0, HERMES_DIR)

import tools.x_search_tool as xst  # noqa: E402

QUERY = ("For each account below, find its most recent post (post, reply or repost) dated on or after "
         "{from_date}. Reply with exactly one line per account: `handle: YYYY-MM-DD` with the date of "
         "the most recent such post, or `handle: none` if it has none. Check every account. "
         "Accounts: {handles}")

# x_search_tool() drops the response's usage block; observe it on the way through.
_last_usage = {}
_post = xst.requests.post


def _observing_post(*args, **kwargs):
    resp = _post(*args, **kwargs)
    try:
        _last_usage["usage"] = resp.json().get("usage")
    except Exception:
        _last_usage["usage"] = None
    return resp


xst.requests.post = _observing_post


def run(job):
    _last_usage.clear()
    raw = xst.x_search_tool(QUERY.format(from_date=job["from_date"], handles=", ".join(job["handles"])),
                            allowed_x_handles=job["handles"], from_date=job["from_date"])
    try:
        r = json.loads(raw)
    except (TypeError, ValueError):
        r = {"success": False, "error": str(raw)[:300]}
    citations = [c.get("url", "") for c in r.get("inline_citations") or [] if isinstance(c, dict)]
    citations += [c if isinstance(c, str) else json.dumps(c) for c in r.get("citations") or []]
    return {"handles": job["handles"], "ok": bool(r.get("success")), "answer": r.get("answer", ""),
            "citations": citations, "degraded": r.get("degraded"),
            "usage": _last_usage.get("usage"), "error": r.get("error")}


def main():
    for job in json.load(sys.stdin):
        try:
            out = run(job)
        except Exception as e:  # report and keep going with the next batch
            out = {"handles": job.get("handles", []), "ok": False, "error": f"{type(e).__name__}: {e}"}
        print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
