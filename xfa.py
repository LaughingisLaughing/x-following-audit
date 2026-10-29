#!/usr/bin/env python3
"""
x-following-audit (xfa): find accounts in an X following list worth unfollowing
================================================================================
Script behind the x-following-audit skill. Replaces the retired web tool
"X Follower Tool" (twitter-unfollow.up.railway.app, Railway service
twitter-following-analyzer).

Same rules as the web tool (inactive / low activity / optional low interaction),
checked through a funnel so the cheapest source decides first and Grok only
sees what is left:

  L0  free            protected, never posted, statuses_count delta vs. snapshots
  L1  --engine api    twitterapi.io last 20 tweets per account (0 AI tokens)
      --engine grok   Hermes x_search called directly, 10 handles per call,
                      post dates verified from the cited status IDs
      --engine none   write compact prompts to paste into grok.com

Stdlib only, so anyone can run it with their own twitterapi.io key: run it in
a terminal and paste the key when asked, or export TWITTERAPI_IO_KEY first.
Reports go to ~/Downloads/x-following-audit, never into the skill folder.
"""

import argparse
import csv
import getpass
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

VERSION = "0.3.0"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = Path("~/Downloads/x-following-audit").expanduser()
LANG = "zh"  # set from --lang; picks the language of progress and error messages

API_BASE = "https://api.twitterapi.io"
FOLLOWINGS_PAGE_SIZE = 200
VAULT_FILE = Path(os.environ.get("XFA_VAULT", "~/Workspaces/_shared/.env.vault")).expanduser()
VAULT_KEYS = ("TWITTER_API_KEY", "TWITTER_API_KEY_FALLBACK")  # primary first, fallback on 401/402
CACHE_VAULT_DIR = Path(os.environ.get("CACHE_VAULT_DIR", "~/Workspaces/cache-vault")).expanduser()
HERMES_DIR = Path(os.environ.get("HERMES_AGENT_DIR", "~/.hermes/hermes-agent")).expanduser()

NS = "x-follower"                 # cache-vault namespace
FOLLOWINGS_TTL = 7 * 86400
ACTIVITY_TTL = 7 * 86400
INTERACTIONS_TTL = 86400
GROK_BATCH = 10                   # Hermes x_search accepts at most 10 allowed_x_handles
GROK_COM_CHUNK = 100              # handles per pasted grok.com prompt
TWITTER_EPOCH_MS = 1288834974657
FIRST_SNOWFLAKE_ID = 29_700_000_000  # IDs below this predate snowflake (Nov 2010)


# ============================================================================
# Small helpers
# ============================================================================

def t(zh, en):
    return en if LANG == "en" else zh


def utcnow():
    return datetime.now(timezone.utc)


def parse_iso(s):
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def snowflake_time(tweet_id):
    """Exact post time encoded in an X status ID, or None for pre-snowflake IDs."""
    try:
        n = int(tweet_id)
    except (TypeError, ValueError):
        return None
    if n < FIRST_SNOWFLAKE_ID:
        return None
    return datetime.fromtimestamp(((n >> 22) + TWITTER_EPOCH_MS) / 1000, tz=timezone.utc)


def tweet_time(t):
    d = snowflake_time(t.get("id"))
    if d:
        return d
    raw = t.get("createdAt") or t.get("created_at") or ""
    for parse in (lambda s: datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y"),
                  parsedate_to_datetime, datetime.fromisoformat):
        try:
            d = parse(raw)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def read_vault(path, names):
    """Read NAME=value lines from the workspace vault without exporting anything."""
    found = {}
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*?)\s*$")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = pattern.match(line)
        if m and m.group(1) in names:
            found[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return found


# ============================================================================
# Cache (cache-vault when present, in-memory otherwise)
# ============================================================================

class Cache:
    def __init__(self, enabled=True):
        self.vault = None
        self.mem = {}
        if enabled and (CACHE_VAULT_DIR / "cache_vault" / "store.py").exists():
            sys.path.insert(0, str(CACHE_VAULT_DIR))
            try:
                from cache_vault.store import Vault
                self.vault = Vault(CACHE_VAULT_DIR, quiet=True)
            except Exception as e:  # keep working without a cache
                print(t(f"cache-vault 不可用（{e}），本次不缓存", f"cache-vault unavailable ({e}); running without a cache"),
                      file=sys.stderr)
            finally:
                sys.path.remove(str(CACHE_VAULT_DIR))

    @property
    def backend(self):
        return f"cache-vault:{NS}" if self.vault else "memory"

    def get(self, key):
        if self.vault:
            e = self.vault.get_fresh(key)
            return e.value if e else None
        hit = self.mem.get(key)
        if hit and (hit[1] is None or hit[1] > time.time()):
            return hit[0]
        return None

    def put(self, key, value, ttl=None):
        if self.vault:
            self.vault.put(key, namespace=NS, value=value, ttl_seconds=ttl)
        else:
            self.mem[key] = (value, time.time() + ttl if ttl else None)

    def items(self, prefix):
        if self.vault:
            return [(e.key, e.value) for e in self.vault.list_entries(NS) if e.key.startswith(prefix)]
        return [(k, v[0]) for k, v in self.mem.items() if k.startswith(prefix)]


# ============================================================================
# twitterapi.io client
# ============================================================================

class ApiError(Exception):
    pass


class TwitterApi:
    """Minimal twitterapi.io client: retries 429/5xx, falls back to the next key on 401/402."""

    def __init__(self, keys):
        self.keys = list(keys)  # [(label, key)]
        self.i = 0
        self.lock = threading.Lock()
        self.calls = 0

    @property
    def label(self):
        return self.keys[self.i][0]

    def get(self, path, params=None, retries=4):
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        last_err = "unknown error"
        for attempt in range(retries):
            with self.lock:
                idx = self.i
                key = self.keys[idx][1]
                self.calls += 1
            req = urllib.request.Request(url, headers={
                "x-api-key": key, "User-Agent": f"x-following-audit/{VERSION}"})
            try:
                # Normal replies take ~2s; a short timeout keeps a stalled proxy hop from
                # pinning every worker for minutes.
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code in (401, 402) and self._switch(idx):
                    continue
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise ApiError(f"{last_err} on {path}")
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                last_err = type(e).__name__
                time.sleep(1 + attempt)
                continue
            if isinstance(body, dict) and body.get("status") == "error":
                raise ApiError(str(body.get("msg") or body.get("message") or "error"))
            return body
        raise ApiError(f"{last_err} on {path} (gave up)")

    def _switch(self, idx):
        with self.lock:
            if self.i != idx:          # another worker already switched
                return True
            if self.i + 1 < len(self.keys):
                self.i += 1
                old, new = self.keys[idx][0], self.keys[self.i][0]
                print(t(f"⚠️  {old} 额度用完或失效，改用 {new}", f"⚠️  {old} is out of credits or invalid; switching to {new}"),
                      file=sys.stderr)
                return True
            return False

    def credits(self):
        try:
            return int(self.get("/oapi/my/info", retries=2).get("recharge_credits"))
        except (ApiError, TypeError, ValueError):
            return None


def load_keys():
    env = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
    if env:
        return [("TWITTERAPI_IO_KEY", env)]
    if VAULT_FILE.exists():
        found = read_vault(VAULT_FILE, VAULT_KEYS)
        keys = [(n, found[n]) for n in VAULT_KEYS if found.get(n)]
        if keys:
            return keys
    if sys.stdin.isatty():
        k = getpass.getpass("twitterapi.io API key（输入时不显示）: ").strip()
        if k:
            return [("typed key", k)]
    return []


# ============================================================================
# Data fetching
# ============================================================================

def compact_user(u):
    h = u.get("userName") or u.get("screen_name") or u.get("username") or ""
    return {
        "h": h,
        "id": str(u.get("id") or u.get("id_str") or ""),
        "name": u.get("name") or h,
        "statuses": int(u.get("statuses_count") or u.get("statusesCount") or 0),
        "followers": int(u.get("followers_count") or u.get("followers") or 0),
        "protected": bool(u.get("protected")),
    }


def fetch_following_count(api, target):
    """The Following number X shows on the target's profile, or None."""
    try:
        return int((api.get("/twitter/user/info", {"userName": target}).get("data") or {}).get("following"))
    except (ApiError, TypeError, ValueError):
        return None


def fetch_followings(api, cache, target, refresh, log):
    """Followings list, cached 7 days; an interrupted fetch resumes from its cursor."""
    key = f"{NS}:followings:{target.lower()}:v1"
    data = None if refresh else cache.get(key)
    if data and data.get("complete"):
        return data, "cache"
    users = list(data["users"]) if data else []
    cursor = data.get("cursor", "") if data else ""
    seen = {u["h"].lower() for u in users}
    complete = False
    while True:
        params = {"userName": target, "pageSize": FOLLOWINGS_PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        try:
            d = api.get("/twitter/user/followings", params)
        except ApiError as e:
            log(t(f"拉关注名单中断：{e}（已拿到 {len(users)} 个）", f"Following list fetch stopped: {e} ({len(users)} fetched)"))
            break
        batch = d.get("followings") or []
        for raw in batch:
            u = compact_user(raw)
            if u["h"] and u["h"].lower() not in seen:
                seen.add(u["h"].lower())
                users.append(u)
        cursor = d.get("next_cursor") or ""
        if not batch or not d.get("has_next_page") or not cursor:
            complete, cursor = True, ""
            break
        log(t(f"  关注名单：已拿到 {len(users)} 个…", f"  Following list: {len(users)} so far…"))
    data = {"users": users, "complete": complete, "cursor": cursor,
            "fetched": utcnow().isoformat(timespec="seconds")}
    if complete:
        # read right after the list, so following_gap compares two numbers from the same moment
        data["x_following"] = fetch_following_count(api, target)
    if users:
        cache.put(key, data, FOLLOWINGS_TTL)
    return data, "api"


def following_gap(fdata):
    """How many more accounts the API list holds than the Following number X shows.

    twitterapi.io's list can include accounts that are no longer followed: on 2026-10-23
    it had 3,791 while the profile showed 3,756, and the unfollow candidates turned out
    to hold 36 accounts no longer followed. Only a complete list read together with the
    count is compared; a list cached before this check has no count."""
    shown = fdata.get("x_following")
    if not fdata.get("complete") or shown is None:
        return 0
    return max(0, len(fdata["users"]) - shown)


def fetch_activity_api(api, user):
    """Dates of the account's latest 20 posts (incl. replies and reposts), newest first.

    last_tweets now and then answers an empty list for an account that does post
    (seen under load on 2026-10-23), so an empty answer is retried once and then
    cross-checked with a from: search before it counts as "no visible posts"."""
    params = {"includeReplies": "true"}
    if user.get("id"):
        params["userId"] = user["id"]
    else:
        params["userName"] = user["h"]
    tweets = []
    for _ in range(2):
        d = api.get("/twitter/user/last_tweets", params)
        tweets = (d.get("data") or {}).get("tweets") or []
        if tweets:
            break
    if not tweets:
        d = api.get("/twitter/tweet/advanced_search", {"query": f"from:{user['h']}", "queryType": "Latest"})
        tweets = d.get("tweets") or []
    dates = sorted((x for x in (tweet_time(t) for t in tweets) if x), reverse=True)
    act = {"src": "api", "dates": [x.isoformat(timespec="seconds") for x in dates],
           "checked": utcnow().isoformat(timespec="seconds")}
    if not dates:
        act["confirmed_empty"] = True
    return act


def fetch_interactions(api, cache, target, days, max_pages, log):
    """Who replied to or mentioned the target recently: one paged search for the whole
    list, instead of the web tool's two searches per followed account."""
    key = f"{NS}:interactions:{target.lower()}:{days}d:v1"
    hit = cache.get(key)
    if hit:
        return hit
    since = (utcnow() - timedelta(days=days)).date().isoformat()
    query = f"(to:{target} OR @{target}) since:{since}"
    counts, cursor, pages, oldest, truncated = {}, "", 0, None, False
    while True:
        params = {"query": query, "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        try:
            d = api.get("/twitter/tweet/advanced_search", params)
        except ApiError as e:
            log(t(f"互动搜索中断：{e}（按已拿到的 {pages} 页计算）", f"Interaction search stopped: {e} (using the {pages} pages fetched)"))
            truncated = True
            break
        pages += 1
        tweets = d.get("tweets") or []
        for t in tweets:
            author = ((t.get("author") or {}).get("userName") or "").lower()
            if author and author != target.lower():
                counts[author] = counts.get(author, 0) + 1
            tt = tweet_time(t)
            if tt and (oldest is None or tt < oldest):
                oldest = tt
        cursor = d.get("next_cursor") or ""
        if not tweets or not d.get("has_next_page") or not cursor:
            break
        if pages >= max_pages:
            truncated = True
            break
    result = {"since": since, "pages": pages, "truncated": truncated, "counts": counts,
              "covered_from": (oldest.date().isoformat() if truncated and oldest else since)}
    cache.put(key, result, INTERACTIONS_TTL)
    return result


# ============================================================================
# Grok engine (Hermes x_search, called directly through grok_bridge.py)
# ============================================================================

GROK_LINE_RE = re.compile(r"@?([A-Za-z0-9_]{1,15})\W{0,4}[:：]\W{0,4}(\d{4}-\d{2}-\d{2}|none)", re.I)
STATUS_URL_RE = re.compile(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)", re.I)


def parse_grok_answer(handles, answer, citations, from_date):
    """Map each handle to {'last', 'verified'} or leave it out when Grok did not answer.

    A post counts as verified only when a cited status URL belongs to that handle
    and its snowflake date is inside the window, so Grok's own wording is never
    trusted for a positive. 'none' answers stay unverified negatives."""
    want = {h.lower(): h for h in handles}
    floor = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
    verified = {}
    for text in list(citations) + [answer or ""]:
        for h, sid in STATUS_URL_RE.findall(text or ""):
            t = snowflake_time(sid)
            if h.lower() in want and t and t >= floor:
                if h.lower() not in verified or t > verified[h.lower()]:
                    verified[h.lower()] = t
    said = {}
    for m in GROK_LINE_RE.finditer(answer or ""):
        h = m.group(1).lower()
        if h in want and h not in said:
            said[h] = None if m.group(2).lower() == "none" else m.group(2)
    out = {}
    for h in want:
        if h in verified:
            out[h] = {"last": verified[h].date().isoformat(), "verified": True}
        elif said.get(h) and said[h] >= from_date:
            out[h] = {"last": said[h], "verified": False}
        elif h in said:
            out[h] = {"last": None, "verified": False}
    return out


def grok_check(users, from_date, batch_size, log):
    """Run batches through Hermes' x_search sequentially in one bridge process
    (one process = no concurrent OAuth refreshes). Returns (results, tokens, calls)."""
    python = HERMES_DIR / "venv" / "bin" / "python3"
    if not python.exists():
        raise RuntimeError(t(f"找不到 Hermes（{python}），改用 --engine none 导出 grok.com 指令",
                             f"Hermes not found ({python}); use --engine none to export grok.com prompts instead"))
    jobs = [{"handles": [u["h"] for u in users[i:i + batch_size]], "from_date": from_date}
            for i in range(0, len(users), batch_size)]
    proc = subprocess.Popen([str(python), str(SCRIPT_DIR / "grok_bridge.py")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, cwd=str(HERMES_DIR))
    proc.stdin.write(json.dumps(jobs))
    proc.stdin.close()
    results, tokens, done = {}, 0, 0
    for line in proc.stdout:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        done += 1
        usage = r.get("usage") or {}
        tokens += int(usage.get("total_tokens") or 0)
        if r.get("ok"):
            results.update(parse_grok_answer(r["handles"], r.get("answer", ""),
                                             r.get("citations") or [], from_date))
        else:
            err = (r.get('error') or '')[:120]
            log(t(f"  Grok 第 {done} 批失败：{err}", f"  Grok batch {done} failed: {err}"))
        log(t(f"  Grok：{done}/{len(jobs)} 批，累计 {tokens} token", f"  Grok: batch {done}/{len(jobs)}, {tokens} tokens so far"))
    proc.wait()
    if proc.returncode not in (0, None) and not results:
        raise RuntimeError(t(f"Grok 桥接进程退出码 {proc.returncode}，Hermes 内部接口可能变了；可改用 --engine none",
                             f"Grok bridge exited with code {proc.returncode}; Hermes internals may have changed. "
                             "Use --engine none instead"))
    return results, tokens, len(jobs)


# ============================================================================
# Rules
# ============================================================================

def load_snapshots(cache, target):
    snaps = []
    for _, v in cache.items(f"{NS}:snapshot:{target.lower()}:"):
        if v and v.get("date") and v.get("counts"):
            snaps.append((parse_iso(v["date"]), v["counts"]))
    return sorted(snaps, key=lambda s: s[0])


def snapshot_verdict(user, snaps, cfg, as_of):
    """Decide from statuses_count growth since an earlier run, at zero cost.

    statuses_count only rises when the account posts, replies or reposts, so
      * >= low_tweets new posts within the inactive window -> neither inactive nor low activity
      * no change for at least the inactive window          -> inactive
    Anything else needs a real check."""
    h = user["h"].lower()
    inactive_days = cfg.inactive_months * 30
    for snap_date, counts in snaps:                      # oldest first
        if h not in counts:
            continue
        age = (as_of - snap_date).days
        delta = user["statuses"] - counts[h]
        if inactive_days <= age and delta == 0:
            reasons = ["inactive"] + (["low_activity"] if age >= cfg.low_days else [])
            return reasons, f"推文数 {age} 天没变"
        if age <= inactive_days and delta >= cfg.low_tweets:
            return [], f"{age} 天内新增 {delta} 条"
    return None


def judge_api(act, cfg, now):
    dates = [parse_iso(x) for x in act.get("dates") or []]
    last = dates[0] if dates else None
    n_low = sum(1 for d in dates if d >= now - timedelta(days=cfg.low_days))
    reasons = []
    if last is None or last < now - timedelta(days=cfg.inactive_months * 30):
        reasons.append("inactive")
    if n_low < cfg.low_tweets:
        reasons.append("low_activity")
    return reasons, (last.date().isoformat() if last else None), n_low


def judge_grok(res, inactive_cut):
    """Grok only answers "posted since the cut or not", so it cannot judge low activity."""
    if res["last"] and res["last"] >= inactive_cut:
        return [], res["last"]
    return ["inactive"], res["last"]


def mark_low_interaction(rows, counts, minimum):
    """Only accounts with a known activity verdict get the flag, so an unchecked
    account never shows up as "only low interaction"."""
    for r in rows:
        if r["status"] != "decided":
            continue
        r["inter"] = counts.get(r["h"].lower(), 0)
        if r["inter"] < minimum:
            r["reasons"] = r["reasons"] + ["low_interaction"]


def grok_basis(res):
    if res["verified"]:
        return "Grok（有推文链接）"
    return "Grok（无链接）" if res["last"] else "Grok 没找到帖子"


# ============================================================================
# Report
# ============================================================================

def write_reports(rows, meta, out_dir, target, stem):
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, csv_path = out_dir / f"{stem}.md", out_dir / f"{stem}.csv"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["handle", "name", "followers", "statuses", "status", "last_post", "posts_in_window",
                    "interactions", "reasons", "basis", "url"])
        for r in rows:
            w.writerow([r["h"], r["name"], r["followers"], r["statuses"], r["status"], r.get("last") or "",
                        "" if r.get("n_low") is None else r["n_low"],
                        "" if r.get("inter") is None else r["inter"],
                        ";".join(r["reasons"]), r["basis"], f"https://x.com/{r['h']}"])

    with_inter = bool(meta.get("interaction_label"))

    def table(items):
        head = "| # | 账号 | 名称 | 粉丝 | 最后发帖 | 一年内帖数 |" + (" 和你互动 |" if with_inter else "") + " 依据 |"
        lines = [head, "|" + "---|" * head.count(" |")]
        for i, r in enumerate(items, 1):
            name = r["name"].replace("|", "/")[:40]
            n_low = "" if r.get("n_low") is None else str(r["n_low"])
            inter = (f" {r['inter']} |" if r.get("inter") is not None else " |") if with_inter else ""
            lines.append(f"| {i} | [@{r['h']}](https://x.com/{r['h']}) | {name} | {r['followers']:,} | "
                         f"{r.get('last') or '无'} | {n_low} |{inter} {r['basis']} |")
        return "\n".join(lines)

    inactive = sorted([r for r in rows if "inactive" in r["reasons"]],
                      key=lambda r: (r.get("last") or "", r["followers"]))
    low = sorted([r for r in rows if "low_activity" in r["reasons"] and "inactive" not in r["reasons"]],
                 key=lambda r: (r.get("n_low") or 0, r.get("last") or ""))
    inter_only = sorted([r for r in rows if r["reasons"] == ["low_interaction"]],
                        key=lambda r: r["followers"])
    unchecked = [r for r in rows if r["status"] in ("protected", "error")]
    n_pending = sum(1 for r in rows if r["status"] == "pending")

    c = meta["counts"]
    gap_note = [] if not meta.get("gap") else [
        f"> ⚠️ 接口名单有 {meta['listed']:,} 个，比 X 显示的关注数（{meta['x_following']:,}）多 {meta['gap']} 个。"
        "多出来的多半是你已经不再关注的账号，会混在下面的候选里，实测集中在「不活跃」表最前面"
        "（最后发帖为「无」或很早的账号）。取关时按钮显示「关注」而不是「正在关注」的，直接跳过。",
        "",
    ]
    parts = [
        f"# @{target} 关注列表检查（{meta['date']}）",
        "",
        f"- 关注总数 {c['total']}，已判定 {c['decided']}，未能检查 {len(unchecked)}，未查 {n_pending}",
        f"- 规则：{meta['rules']}",
        f"- 判定来源：{meta['sources']}",
        f"- 成本：{meta['cost']}",
        "",
        "> 取关请放慢：每小时不超过 20 个，每天不超过 100 个，中间留间隔，避免触发 X 风控。",
        "",
        *gap_note,
        f"## 不活跃：{meta['inactive_label']}（{len(inactive)} 个）",
        "",
        table(inactive) if inactive else "无",
        "",
        f"## 低活跃：{meta['low_label']}（{len(low)} 个）",
        "",
        table(low) if low else "无",
    ]
    if with_inter:
        parts += ["", f"## 只因低互动：{meta['interaction_label']}（{len(inter_only)} 个）", ""]
        if len(inter_only) <= 200:
            parts.append(table(inter_only) if inter_only else "无")
        else:
            parts.append("这些账号仍在正常发帖，只是很少回复或提到你。关注的人一多，大部分账号都会落在这里，"
                         "单看意义不大；上面两张表里的「和你互动」一列更有用：常和你互动的，可能是值得保留的朋友。"
                         "完整名单见 CSV 的 reasons 列。")
    if unchecked:
        parts += ["", f"## 未能检查（{len(unchecked)} 个）", ""]
        for r in unchecked[:200]:
            why = "私密账号，看不到帖子" if r["status"] == "protected" else r["basis"]
            parts.append(f"- [@{r['h']}](https://x.com/{r['h']})：{why}")
        if len(unchecked) > 200:
            parts.append(f"- 其余 {len(unchecked) - 200} 个见 CSV")
    if n_pending:
        parts += ["", f"## 未查（{n_pending} 个）", "",
                  "这些账号本次没有检查，用 grok.com 指令文件逐段检查，或换 `--engine api` 再跑。"]
    md_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return md_path, csv_path


GROK_COM_PROMPT = {
    "zh": ("请逐个检查下面全部 {n} 个 X 账号（不要抽样、不要跳过），用 X 搜索看每个账号在 {since} 之后"
           "有没有发过帖子（原帖、回复、转发都算）。\n"
           "只输出在 {since} 之后一条帖子都没有的账号，每行一个 @handle；最后一行写「已检查 {n}/{n}」。\n\n"
           "账号：\n{handles}\n"),
    "en": ("Check ALL {n} X accounts below one by one (no sampling, no skipping). Use X search to see whether "
           "each account posted anything (post, reply or repost) after {since}.\n"
           "Output only the accounts with no post at all after {since}, one @handle per line, and end with "
           "\"checked {n}/{n}\".\n\nAccounts:\n{handles}\n"),
}


def write_grok_prompts(users, since, out_dir, stem, lang):
    path = out_dir / f"{stem}-grok-prompts.txt"
    chunks = [users[i:i + GROK_COM_CHUNK] for i in range(0, len(users), GROK_COM_CHUNK)]
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        handles = "\n".join(f"@{u['h']}" for u in chunk)
        head = f"===== 第 {i}/{len(chunks)} 段 =====" if lang == "zh" else f"===== part {i}/{len(chunks)} ====="
        blocks.append(head + "\n" + GROK_COM_PROMPT[lang].format(n=len(chunk), since=since, handles=handles))
    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path, len(chunks)


# ============================================================================
# audit
# ============================================================================

def audit(cfg):
    target = cfg.handle.lstrip("@").strip()
    log = (lambda *_: None) if cfg.quiet else (lambda m: print(m, file=sys.stderr, flush=True))
    keys = load_keys()
    if not keys:
        sys.exit("没有 twitterapi.io key：设置环境变量 TWITTERAPI_IO_KEY，或在终端里运行以手动输入。\n"
                 "No twitterapi.io key: set TWITTERAPI_IO_KEY, or run this in a terminal and paste the key when asked.")
    api = TwitterApi(keys)
    cache = Cache(enabled=not cfg.no_cache)
    credits_before = api.credits()

    # Phase 1: followings list
    log(t(f"拉取 @{target} 的关注名单（缓存 {cache.backend}）…", f"Fetching @{target}'s following list (cache: {cache.backend})…"))
    fdata, fsrc = fetch_followings(api, cache, target, cfg.refresh, log)
    users = fdata["users"]
    if not users:
        sys.exit(f"拿不到 @{target} 的关注名单：账号名是否正确？是否私密？\n"
                 f"Could not get @{target}'s following list: is the handle right, and is the account public?")
    if not fdata["complete"]:
        log(t(f"⚠️  名单未拉完（{len(users)} 个），先按已拿到的分析",
              f"⚠️  Following list incomplete ({len(users)} accounts); analysing what was fetched"))
    gap = following_gap(fdata)
    if fdata.get("x_following") is not None:
        log(t(f"关注数核对：接口名单 {len(users)} 个，X 显示关注 {fdata['x_following']} 个",
              f"Following count check: {len(users)} on the API list, {fdata['x_following']} shown on X"))
    if cfg.limit:
        users = users[:cfg.limit]
    as_of = parse_iso(fdata["fetched"])
    now = utcnow()
    # a --limit trial run gets its own file name so it never overwrites the day's full report
    stem = f"{target.lower()}-{now:%Y%m%d}" + (f"-limit{cfg.limit}" if cfg.limit else "")
    inactive_cut = (now - timedelta(days=cfg.inactive_months * 30)).date().isoformat()
    snaps = [s for s in load_snapshots(cache, target) if s[0] < as_of - timedelta(hours=12)]

    # Phase 2: zero-cost layer
    rows, pending = [], []
    stats = {"free": 0, "snapshot": 0, "api": 0, "grok": 0, "cache": 0}
    for u in users:
        row = dict(u, reasons=[], last=None, n_low=None, inter=None, basis="", status="pending")
        rows.append(row)
        if u["protected"]:
            row.update(status="protected", basis="私密")
            continue
        if u["statuses"] == 0:
            row.update(status="decided", reasons=["inactive", "low_activity"], basis="从没发过帖")
            stats["free"] += 1
            continue
        if cfg.skip_heavy and u["statuses"] >= cfg.skip_heavy:
            row.update(status="decided", basis=f"总推文数 ≥ {cfg.skip_heavy}，视为活跃")
            stats["free"] += 1
            continue
        act = cache.get(f"{NS}:activity:{u['h'].lower()}:v1")
        if act and (act.get("dates") or act.get("confirmed_empty")):   # unconfirmed empties get re-checked
            reasons, last, n_low = judge_api(act, cfg, now)
            row.update(status="decided", reasons=reasons, last=last, n_low=n_low, basis="接口（缓存）")
            stats["cache"] += 1
            continue
        res = cache.get(f"{NS}:grok:{u['h'].lower()}:v1") if cfg.engine != "api" else None
        if res:
            reasons, last = judge_grok(res, inactive_cut)
            row.update(status="decided", reasons=reasons, last=last, basis=grok_basis(res) + "（缓存）")
            stats["cache"] += 1
            continue
        verdict = snapshot_verdict(u, snaps, cfg, as_of)
        if verdict:
            row.update(status="decided", reasons=verdict[0], basis=verdict[1])
            stats["snapshot"] += 1
            continue
        pending.append(row)
    log(t(f"零成本层：{len(users)} 个里 {len(users) - len(pending)} 个已有结论，剩 {len(pending)} 个待查",
          f"Free checks: {len(users) - len(pending)} of {len(users)} decided, {len(pending)} left to check"))

    # Phase 3: engine
    grok_tokens, grok_calls, prompt_file = 0, 0, None
    if pending and cfg.engine == "api":
        log(t(f"用 twitterapi.io 查 {len(pending)} 个账号最近 20 条帖子（{cfg.workers} 并发）…",
              f"Checking the latest 20 posts of {len(pending)} accounts on twitterapi.io ({cfg.workers} in parallel)…"))
        done, errors, started = 0, 0, time.time()
        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            futs = {pool.submit(fetch_activity_api, api, row): row for row in pending}
            for fut in as_completed(futs):
                row = futs[fut]
                done += 1
                try:
                    act = fut.result()
                except ApiError as e:
                    errors += 1
                    row.update(status="error", basis=f"接口报错：{e}")
                else:
                    cache.put(f"{NS}:activity:{row['h'].lower()}:v1", act, ACTIVITY_TTL)
                    reasons, last, n_low = judge_api(act, cfg, now)
                    row.update(status="decided", reasons=reasons, last=last, n_low=n_low, basis="接口")
                    stats["api"] += 1
                if done % 100 == 0 or done == len(pending):
                    mins, secs = divmod(int(time.time() - started), 60)
                    log(t(f"  {done}/{len(pending)}（报错 {errors}，用时 {mins}:{secs:02d}）",
                          f"  {done}/{len(pending)} (errors {errors}, elapsed {mins}:{secs:02d})"))
    to_grok = pending if cfg.engine == "grok" else []
    if cfg.engine == "api" and cfg.grok_fallback:
        to_grok = [r for r in pending if r["status"] == "error"]
    if to_grok:
        log(t(f"用 Grok x_search 查 {len(to_grok)} 个账号在 {inactive_cut} 之后有没有发帖…",
              f"Asking Grok x_search whether {len(to_grok)} accounts posted after {inactive_cut}…"))
        results, grok_tokens, grok_calls = grok_check(to_grok, inactive_cut, GROK_BATCH, log)
        for row in to_grok:
            res = results.get(row["h"].lower())
            if not res:
                continue
            cache.put(f"{NS}:grok:{row['h'].lower()}:v1", dict(res, **{"from": inactive_cut}), ACTIVITY_TTL)
            reasons, last = judge_grok(res, inactive_cut)
            row.update(status="decided", reasons=reasons, last=last, basis=grok_basis(res))
            stats["grok"] += 1

    still = [r for r in rows if r["status"] == "pending"]
    if still and cfg.engine in ("none", "grok"):
        prompt_file, n_chunks = write_grok_prompts(still, inactive_cut, cfg.out, stem, cfg.lang)

    # Phase 4: optional low-interaction check
    inter_label = None
    if cfg.interaction:
        log(t("搜索谁在回复或提到你（低互动检查）…", "Searching who replied to or mentioned you (interaction check)…"))
        inter = fetch_interactions(api, cache, target, cfg.interaction_days, cfg.interaction_pages, log)
        mark_low_interaction(rows, inter["counts"], cfg.interaction_min)
        inter_label = f"{inter['covered_from']} 以来回复 + 提到你少于 {cfg.interaction_min} 次"
        if inter["truncated"]:
            inter_label += f"（只搜了 {inter['pages']} 页）"

    # Snapshot for next time's zero-cost layer (keyed by the list's fetch date, so a cached
    # list just rewrites the same entry)
    if fdata["complete"] and not cfg.limit:
        cache.put(f"{NS}:snapshot:{target.lower()}:{as_of.date().isoformat()}",
                  {"date": as_of.isoformat(timespec="seconds"),
                   "counts": {u["h"].lower(): u["statuses"] for u in users}})

    credits_after = api.credits()
    cost = [f"twitterapi.io {api.calls} 次调用"]
    if credits_before is not None and credits_after is not None:
        cost.append(f"余额 {credits_before:,} → {credits_after:,}（{api.label}，结算可能延迟）")
    cost.append(f"Grok {grok_tokens:,} token（{grok_calls} 次 x_search）" if grok_calls else "Grok 0 token")
    decided = [r for r in rows if r["status"] == "decided"]
    meta = {
        "date": now.date().isoformat(),
        "rules": (f"{cfg.inactive_months} 个月没发帖算不活跃；{cfg.low_days} 天内少于 {cfg.low_tweets} 条算低活跃"
                  + (f"；回复 + 提到你少于 {cfg.interaction_min} 次算低互动" if cfg.interaction else "")),
        "sources": (f"零成本 {stats['free']}，快照差分 {stats['snapshot']}，接口缓存 {stats['cache']}，"
                    f"接口 {stats['api']}，Grok {stats['grok']}"),
        "cost": "；".join(cost),
        "inactive_label": f"{inactive_cut} 之后没发过帖",
        "low_label": f"{cfg.low_days} 天内少于 {cfg.low_tweets} 条",
        "interaction_label": inter_label,
        "counts": {"total": len(users), "decided": len(decided)},
        "gap": gap, "listed": len(fdata["users"]), "x_following": fdata.get("x_following"),
    }
    md_path, csv_path = write_reports(rows, meta, cfg.out, target, stem)

    n_inactive = sum(1 for r in rows if "inactive" in r["reasons"])
    n_low = sum(1 for r in rows if "low_activity" in r["reasons"] and "inactive" not in r["reasons"])
    n_inter = sum(1 for r in rows if r["reasons"] == ["low_interaction"])
    by_status = {s: sum(1 for r in rows if r["status"] == s) for s in ("protected", "error", "pending")}
    if cfg.lang == "en":
        lines = [
            f"@{target} follows {len(users)} accounts; {len(decided)} decided",
            f"Unfollow candidates: {n_inactive} inactive (no post since {inactive_cut}), {n_low} low activity "
            f"(< {cfg.low_tweets} posts in {cfg.low_days} days)"
            + (f", {n_inter} only low interaction" if cfg.interaction else ""),
            f"Not checked: {by_status['protected']} protected, {by_status['error']} API errors, "
            f"{by_status['pending']} pending",
            f"twitterapi.io calls: {api.calls}; Grok tokens: {grok_tokens:,}",
            f"Report (Chinese): {md_path}",
            f"CSV: {csv_path}",
        ]
        if prompt_file:
            lines.append(f"grok.com prompts ({len(still)} accounts, {n_chunks} part{'s' if n_chunks != 1 else ''}, "
                         f"paste one part at a time): {prompt_file}")
    else:
        lines = [
            f"@{target} 关注 {len(users)} 个，已判定 {len(decided)} 个（{meta['sources']}）",
            f"建议取关：不活跃 {n_inactive} 个（{meta['inactive_label']}），低活跃 {n_low} 个（{meta['low_label']}）"
            + (f"，只因低互动 {n_inter} 个" if cfg.interaction else ""),
            f"未能检查：私密 {by_status['protected']}，接口报错 {by_status['error']}，未查 {by_status['pending']}",
            f"成本：{meta['cost']}",
            f"报告：{md_path}",
            f"CSV：{csv_path}",
        ]
        if prompt_file:
            lines.append(f"grok.com 指令（{len(still)} 个未查账号，{n_chunks} 段，逐段贴进 grok.com）：{prompt_file}")
    if gap:
        lines.insert(2, t(f"⚠️  接口名单比 X 显示的关注数（{fdata['x_following']:,}）多 {gap} 个，多出来的多半已经不在你的关注里，"
                          "取关时按钮显示「关注」的直接跳过",
                          f"⚠️  The API list has {gap} more accounts than the {fdata['x_following']:,} X shows you following; "
                          "the extra ones are most likely accounts you no longer follow. When unfollowing, skip any whose "
                          "button says \"Follow\""))
    print("\n".join(lines))


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="xfa", description="找出 X 关注列表里不活跃、低活跃、低互动的账号 / "
                                "find inactive, low-activity and low-interaction accounts in an X following list")
    p.add_argument("--version", action="version", version=f"x-following-audit {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="检查一个账号的关注列表 / audit one account's following list")
    a.add_argument("handle", help="要检查的 X 账号，不带 @ 也行 / X handle to audit, @ optional")
    a.add_argument("--engine", choices=["api", "grok", "none"], default="api",
                   help="零成本层之后用什么查：api=twitterapi.io（默认，0 AI token），grok=Hermes x_search，"
                        "none=只导出 grok.com 指令 / how to check what the free checks leave: api = twitterapi.io "
                        "(default, no AI tokens), grok = Hermes x_search, none = only export grok.com prompts")
    a.add_argument("--inactive-months", type=int, default=6,
                   help="多少个月没发帖算不活跃（默认 6）/ months without posts that count as inactive (default 6)")
    a.add_argument("--low-tweets", type=int, default=5,
                   help="低活跃阈值：窗口内少于几条（默认 5）/ low activity: fewer posts than this in the window (default 5)")
    a.add_argument("--low-days", type=int, default=365,
                   help="低活跃窗口天数（默认 365）/ low-activity window in days (default 365)")
    a.add_argument("--grok-fallback", action="store_true",
                   help="接口报错的账号再交给 Grok x_search 查（仅 --engine api）/ "
                        "re-check accounts the API failed on with Grok x_search (--engine api only)")
    a.add_argument("--interaction", action="store_true",
                   help="同时做低互动检查（默认关）/ also flag accounts that rarely reply to or mention you (off by default)")
    a.add_argument("--interaction-min", type=int, default=3,
                   help="回复 + 提到你少于几次算低互动（默认 3）/ fewer replies + mentions than this = low interaction (default 3)")
    a.add_argument("--interaction-days", type=int, default=365,
                   help="低互动检查往回看几天（默认 365）/ days the interaction check looks back (default 365)")
    a.add_argument("--interaction-pages", type=int, default=50,
                   help="低互动搜索最多翻几页，每页约 20 条（默认 50）/ max search pages of ~20 posts (default 50)")
    a.add_argument("--skip-heavy", type=int, default=0,
                   help="总推文数不少于 N 的账号直接视为活跃、不查（网页版默认 5000），0=不跳过 / "
                        "treat accounts with at least N posts as active without checking (the web app used 5000); 0 = off")
    a.add_argument("--limit", type=int, default=0,
                   help="只查名单前 N 个（最近关注的在前），0=全部 / only check the N most recently followed; 0 = all")
    a.add_argument("--refresh", action="store_true",
                   help="重新拉关注名单（取关一批后用）/ fetch the following list again instead of using the cache")
    a.add_argument("--workers", type=int, default=4, help="接口并发数（默认 4）/ parallel API requests (default 4)")
    a.add_argument("--lang", choices=["zh", "en"], default="zh",
                   help="屏幕输出和 grok.com 指令的语言，报告固定中文 / language of screen output and grok.com prompts; "
                        "the report itself is in Chinese")
    a.add_argument("--out", type=Path, default=DEFAULT_OUT,
                   help="报告目录（默认 ~/Downloads/x-following-audit）/ output folder (default ~/Downloads/x-following-audit)")
    a.add_argument("--no-cache", action="store_true", help="不读写 cache-vault / don't use cache-vault")
    a.add_argument("--quiet", action="store_true", help="不打印进度，只输出摘要 / summary only, no progress")
    cfg = p.parse_args(argv)
    cfg.out = cfg.out.expanduser().resolve()
    global LANG
    LANG = cfg.lang
    if cfg.cmd == "audit":
        audit(cfg)


if __name__ == "__main__":
    main()
