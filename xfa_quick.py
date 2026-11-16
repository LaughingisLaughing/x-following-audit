"""免 key 快速检查 / Quick check without a twitterapi.io key (Grok Bot).

On Grok Bot only the Bot can call its X tools. With write_to_file on, each tool result is saved as a file named by
the SHA-256 of its bytes. This module reads those files strictly and sorts the following list by activity alone:

  1. From the profiles: a current post count of 0, or a most recent post (time read from its snowflake id) at least
     inactive_days old, makes an account a candidate. A candidate is "not enough evidence" until step 2.
  2. The Bot fetches each candidate's timeline (replies and reposts included). Only when the newest post there is
     still inactive_days old (or the timeline is empty and the count is 0) does the account reach tier 2.

Mutuals come from connection_status in the following list, private accounts from the profile. Content rules
(marketing, flooding, replies, reposts, languages, reviews) and the interaction protection are not checked; the
report says so. The run feeds plan / approve / batch / sync unchanged. Nothing here loads an API key or calls
twitterapi.io.

These checks catch files edited after they were saved, wrong or mixed accounts, truncated lists and malformed
data. They cannot prove a file came from a real tool call: a Bot that writes a new, consistently named file is
not detected. The last line of defence stays in the browser script, which opens every account by id and checks
the Following button and the signed-in account before any click."""

import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import xfa
import xfa_tiers as xt
from xfa_tiers import iso, lab, parse_time, read_json, t, tier_label, utcnow, write_json

MODE = "quick"
SNOWFLAKE_EPOCH_MS = 1288834974657            # 2010-11-04T01:42:54.657Z
HASH_NAME = re.compile(r"^[0-9a-f]{64}\.txt$")
ID_RE = re.compile(r"^[0-9]{1,20}$")
USER_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
WRAP_OPEN = '<cursor_untrusted_data_1337 source="CallDynamicTool">'
WRAP_CLOSE = "</cursor_untrusted_data_1337>"
POINTER = re.compile(r"Full text: (?P<path>/[^\s]*?/(?P<hash>[0-9a-f]{64})\.txt)\. To see more")
QUOTA = re.compile(r"X reads left for this user: (\d+) of (\d+) this minute \(resets at ([0-9T:.\-]+Z)\), "
                   r"(\d+) of (\d+) today \(resets at ([0-9T:.\-]+Z)\)\.")
MAX_BYTES = 64 * 1024 * 1024
TOP_KEYS = {"data", "meta", "includes", "errors"}


class QuickError(Exception):
    pass


def need(cond, zh, en):
    if not cond:
        raise QuickError(t(zh, en))


qlab = lab   # the quick reasons live in xfa_tiers.LABELS with the others


# ============================================================================
# Reading the Bot's archive files
# ============================================================================

def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        need(k not in out, f"JSON 里有重复的键 {k!r}", f"Duplicate JSON key {k!r}")
        out[k] = v
    return out


DECODER = json.JSONDecoder(object_pairs_hook=_no_dupes)


def _quota(m):
    return {"minute_left": int(m.group(1)), "minute_max": int(m.group(2)), "minute_resets": m.group(3),
            "day_left": int(m.group(4)), "day_max": int(m.group(5)), "day_resets": m.group(6)}


def same_folder(pointed, actual):
    """The folder a short file points to must be the folder it lies in. XFA_ARCHIVE_MAP=old=new maps a moved
    archive (old is the folder named inside the files, new where they lie now)."""
    pointed = str(pointed)
    for pair in filter(None, os.environ.get("XFA_ARCHIVE_MAP", "").split(";")):
        old, _, new = pair.partition("=")
        if old and new and pointed == old.rstrip("/"):
            pointed = new
    return os.path.realpath(pointed) == os.path.realpath(str(actual))


def read_archive(path, _short=None):
    """One saved tool result → {path, name, sha256, bytes, obj, quota, mtime, via}. The file must be named by the
    SHA-256 of its bytes. A short file (the wrapped, truncated preview) is followed once to the full file in the
    same folder; the full file must be plain JSON, optionally followed by the read-quota sentence."""
    p = Path(path)
    need(not p.is_symlink(), f"{p.name} 是软链接，只收 Bot 归档目录里的原始文件",
         f"{p.name} is a symlink; only the original files in the Bot's archive folder are accepted")
    need(p.is_file(), f"找不到文件 {p}", f"No such file: {p}")
    need(bool(HASH_NAME.match(p.name)), f"{p.name}：文件名不是「内容的 SHA-256 + .txt」，只收 Bot 归档目录里的原始文件",
         f"{p.name}: not named <SHA-256 of its bytes>.txt; only the original files in the Bot's archive folder are accepted")
    need(p.stat().st_size <= MAX_BYTES, f"{p.name} 太大", f"{p.name} is too large")
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    need(digest + ".txt" == p.name, f"{p.name}：内容和文件名的哈希对不上，存下来以后被改过",
         f"{p.name}: its bytes do not hash to its name; it was changed after it was saved")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise QuickError(t(f"{p.name} 不是 UTF-8 文本", f"{p.name} is not UTF-8 text"))
    body = text.strip()
    if body.startswith(WRAP_OPEN):
        need(_short is None, f"{p.name}：完整文件不能再指向别的文件", f"{p.name}: a full file cannot point to another file")
        need(body.endswith(WRAP_CLOSE), f"{p.name}：短文件结尾不对", f"{p.name}: the short file does not end as expected")
        hits = POINTER.findall(body)
        need(len(hits) == 1, f"{p.name}：短文件里要有且只有一句指向完整文件",
             f"{p.name}: a short file must point to exactly one full file")
        ptr, h = hits[0]
        need(same_folder(Path(ptr).parent, p.parent), f"{p.name}：指向的文件不在这个归档目录里",
             f"{p.name}: it points outside its own archive folder")
        full = read_archive(p.parent / f"{h}.txt", _short={"path": str(p), "sha256": digest})
        q = QUOTA.search(body)
        if q and not full["quota"]:
            full["quota"] = _quota(q)
        return full
    need(body.startswith("{"), f"{p.name}：开头不是 JSON", f"{p.name} does not start with JSON")
    try:
        obj, end = DECODER.raw_decode(body)
    except ValueError as e:
        raise QuickError(t(f"{p.name}：JSON 读不出来（{e}）", f"{p.name}: unreadable JSON ({e})"))
    tail = body[end:].strip()
    quota = None
    if tail:
        m = QUOTA.fullmatch(tail)
        need(bool(m), f"{p.name}：JSON 后面有认不出的内容", f"{p.name}: unrecognised text after the JSON")
        quota = _quota(m)
    need(isinstance(obj, dict), f"{p.name}：顶层不是对象", f"{p.name}: the top level is not an object")
    return {"path": str(p), "name": p.name, "sha256": digest, "bytes": raw, "obj": obj, "quota": quota,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc), "via": _short}


# ============================================================================
# Parsing X API v2 results
# ============================================================================

def _top(rec, allowed=TOP_KEYS):
    o = rec["obj"]
    extra = set(o) - allowed
    need(not extra, f"{rec['name']}：多了认不出的顶层字段 {sorted(extra)}", f"{rec['name']}: unknown top-level keys {sorted(extra)}")
    return o


def _id(v, what, name):
    need(isinstance(v, str) and bool(ID_RE.match(v)), f"{name}：{what} 不是数字字符串", f"{name}: {what} is not a numeric string")
    return v


def _handle(v, name):
    need(isinstance(v, str) and bool(USER_RE.match(v)), f"{name}：用户名格式不对", f"{name}: malformed username")
    return v


def _metrics(u, name):
    pm = u.get("public_metrics")
    if pm is None:
        return {}
    need(isinstance(pm, dict), f"{name}：public_metrics 不是对象", f"{name}: public_metrics is not an object")
    out = {}
    for k in ("tweet_count", "followers_count", "following_count"):
        if k in pm:
            need(isinstance(pm[k], int) and not isinstance(pm[k], bool) and pm[k] >= 0,
                 f"{name}：{k} 不是非负整数", f"{name}: {k} is not a non-negative integer")
            out[k] = pm[k]
    return out


def user_record(u, name):
    """The fields this check uses, type-checked. Unknown fields are ignored (they stay in the evidence copy)."""
    need(isinstance(u, dict), f"{name}：账号条目不是对象", f"{name}: an account entry is not an object")
    r = {"id": _id(u.get("id"), "id", name), "h": _handle(u.get("username"), name),
         "name": u.get("name") if isinstance(u.get("name"), str) else ""}
    if "connection_status" in u:
        cs = u["connection_status"]
        need(isinstance(cs, list) and all(isinstance(x, str) for x in cs),
             f"{name}：connection_status 格式不对", f"{name}: malformed connection_status")
        r["follows_you"] = "followed_by" in cs
        r["you_follow"] = "following" in cs
    if "protected" in u:
        need(isinstance(u["protected"], bool), f"{name}：protected 不是布尔值", f"{name}: protected is not a boolean")
        r["protected"] = u["protected"]
    if "created_at" in u:
        need(parse_time(u["created_at"]) is not None, f"{name}：created_at 不是时间", f"{name}: created_at is not a time")
        r["created_at"] = u["created_at"]
    if "most_recent_tweet_id" in u:
        r["last_id"] = _id(u["most_recent_tweet_id"], "most_recent_tweet_id", name)
    m = _metrics(u, name)
    if "tweet_count" in m:
        r["statuses"] = m["tweet_count"]
    if "followers_count" in m:
        r["followers"] = m["followers_count"]
    if "following_count" in m:
        r["following"] = m["following_count"]
    return r


def parse_me(rec):
    o = _top(rec, {"data", "errors"})
    need(not o.get("errors"), f"{rec['name']}：me 返回里有 errors", f"{rec['name']}: the me result has errors")
    d = o.get("data")
    need(isinstance(d, dict), f"{rec['name']}：不是 get_users_me 的返回", f"{rec['name']}: not a get_users_me result")
    return user_record(d, rec["name"])


def parse_following_page(rec):
    o = _top(rec)
    need(not o.get("errors"), f"{rec['name']}：关注名单返回里有 errors", f"{rec['name']}: the following page has errors")
    data, meta = o.get("data", []), o.get("meta")
    need(isinstance(data, list) and isinstance(meta, dict), f"{rec['name']}：不是关注名单的一页", f"{rec['name']}: not a page of the following list")
    rc = meta.get("result_count")
    need(isinstance(rc, int) and rc == len(data), f"{rec['name']}：result_count 和条数对不上",
         f"{rec['name']}: result_count does not match the entries")
    nt = meta.get("next_token")
    need(nt is None or (isinstance(nt, str) and nt), f"{rec['name']}：next_token 格式不对", f"{rec['name']}: malformed next_token")
    users = [user_record(u, rec["name"]) for u in data]
    for u in users:
        need("follows_you" in u, f"{rec['name']}：名单条目缺 connection_status（调用时要带上）",
             f"{rec['name']}: an entry lacks connection_status (request it)")
    return users, nt


def parse_profiles(rec):
    o = _top(rec)
    data = o.get("data", [])
    need(isinstance(data, list), f"{rec['name']}：不是资料的返回", f"{rec['name']}: not a users result")
    errs = o.get("errors") or []
    need(isinstance(errs, list), f"{rec['name']}：errors 格式不对", f"{rec['name']}: malformed errors")
    return [user_record(u, rec["name"]) for u in data], len(errs)


def parse_timeline(rec, uid):
    """Posts of one account: [{id, at, kind}]. Every post must be the account's own, and its created_at must match
    the time inside its snowflake id."""
    o = _top(rec)
    if o.get("errors"):
        return None   # suspended, deactivated, not authorised, or a partial failure: not evidence either way
    data, meta = o.get("data", []), o.get("meta")
    need(isinstance(data, list) and isinstance(meta, dict), f"{rec['name']}：不是时间线的返回", f"{rec['name']}: not a timeline result")
    rc = meta.get("result_count")
    need(isinstance(rc, int) and not isinstance(rc, bool) and rc == len(data),
         f"{rec['name']}：时间线没有 result_count，或和条数对不上", f"{rec['name']}: no result_count, or it does not match the posts")
    posts = []
    for p in data:
        need(isinstance(p, dict), f"{rec['name']}：帖子条目不是对象", f"{rec['name']}: a post is not an object")
        pid = _id(p.get("id"), "id", rec["name"])
        need(p.get("author_id") == uid, f"{rec['name']}：时间线里有别的账号的帖子（{uid} 和文件对不上）",
             f"{rec['name']}: the timeline holds another account's posts ({uid} does not match the file)")
        at = parse_time(p.get("created_at"))
        need(at is not None, f"{rec['name']}：帖子没有发布时间（调用时要带 created_at）",
             f"{rec['name']}: a post has no created_at (request it)")
        snow = snowflake_time(pid)
        need(snow is None or abs((snow - at).total_seconds()) <= 2, f"{rec['name']}：帖子的时间和它的 id 对不上",
             f"{rec['name']}: a post's time does not match its id")
        kinds = {r.get("type") for r in p.get("referenced_tweets") or [] if isinstance(r, dict)}
        kind = "rt" if "retweeted" in kinds else "reply" if "replied_to" in kinds else "quote" if "quoted" in kinds else "post"
        posts.append({"id": pid, "at": iso(at), "kind": kind})
    return posts


def snowflake_time(sid):
    """The creation time inside a snowflake id; None for ids from before snowflakes (November 2010)."""
    ms = (int(sid) >> 22) + SNOWFLAKE_EPOCH_MS
    if ms < SNOWFLAKE_EPOCH_MS + 86400000:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ============================================================================
# Collecting and checking the evidence
# ============================================================================

def collect(ctx, cfg, now):
    """Read every file, check it, and return (owner, accounts, list_info, evidence). Raises QuickError."""
    evidence = []

    def take(path, role, key=None):
        rec = read_archive(path)
        evidence.append({"role": role, "key": key, "rec": rec})
        return rec

    me = parse_me(take(cfg.me, "me"))
    need(me["h"].lower() == ctx.handle.lower(), f"me 文件里的账号是 @{me['h']}，不是 @{ctx.handle}",
         f"The me file is @{me['h']}, not @{ctx.handle}")
    known = (ctx.profile().get("owner") or {}).get("id")
    need(not known or str(known) == me["id"], "个人设置里记下的账号 id 和 me 文件不一致：确认 Bot 连接的是同一个 X 账号",
         "The account id in the profile differs from the me file: check the Bot's connected X account")

    # Pages in the order they were fetched: the first as a file, every later one as token=file, where token is the
    # pagination_token the Bot sent for it. Each token must be the previous page's next_token, so a skipped,
    # repeated or reordered page breaks the chain instead of passing as a shorter list.
    pages = []
    for i, item in enumerate(cfg.following):
        token, path = (None, item) if i == 0 else tuple(item.split("=", 1)) if "=" in item else (None, item)
        need(i == 0 or token, f"--following 第 {i + 1} 项要写成 翻页token=文件（token 是请求这一页时用的 pagination_token）",
             f"--following item {i + 1} must be token=file (the pagination_token used to request that page)")
        rec = take(path, "following", token or "first")
        users, nt = parse_following_page(rec)
        pages.append({"token": token, "sha": rec["sha256"], "next": nt, "users": users, "mtime": rec["mtime"]})
    need(len({pg["sha"] for pg in pages}) == len(pages), "同一页给了两次", "The same page was given twice")
    for k in range(1, len(pages)):
        need(pages[k]["token"] == pages[k - 1]["next"], f"第 {k + 1} 页的翻页 token 不是第 {k} 页给的 next_token：漏页、重复或顺序不对",
             f"Page {k + 1}'s token is not page {k}'s next_token: a page is missing, repeated or out of order")
    times = [pg["mtime"] for pg in pages]
    need(max(times) - min(times) <= timedelta(hours=6), "名单的各页不是同一次拉的（前后相隔超过 6 小时），重新拉一遍",
         "The pages were not fetched together (more than 6 hours apart): fetch the list again")
    need(now - max(times) <= timedelta(days=3), "名单是 3 天以前拉的，太旧了，重新拉一遍",
         "The list was fetched more than 3 days ago: fetch it again")
    follows = {}
    for pg in pages:
        for u in pg["users"]:
            need(u.get("you_follow"), "名单里有不是「我关注的」账号：这不是 get_users_me_following 的返回",
                 "An entry is not marked as followed by me: this is not a get_users_me_following result")
            need(u["id"] not in follows, "同一个账号出现在两页里", "One account appears on two pages")
            need(u["id"] != me["id"], "关注名单里出现了本人", "The owner appears in their own following list")
            follows[u["id"]] = u
    need(follows, "关注名单是空的", "The following list is empty")
    chain_done = pages[-1]["next"] is None

    owner = dict(me)
    for path in cfg.profiles or []:
        rec = take(path, "profiles")
        need(min(times) - timedelta(hours=1) <= rec["mtime"] <= max(times) + timedelta(hours=6),
             "资料文件和名单不是同一次拉的（相差超过几个小时），重新拉", "The profiles were not fetched with the list: fetch them again")
        users, _errors = parse_profiles(rec)
        for u in users:
            if u["id"] == me["id"]:
                need(u["h"].lower() == me["h"].lower(), "资料里本人的用户名和 me 文件不一致",
                     "The owner's username in the profiles differs from the me file")
                owner.update({k: v for k, v in u.items() if k not in ("follows_you", "you_follow")})
                continue
            f = follows.get(u["id"])
            need(f is not None, "资料里有不在关注名单上的账号：分批时只能放名单里的用户名（和你自己）",
                 "The profiles hold an account that is not in the following list: batch only listed usernames (and your own)")
            need(f["h"].lower() == u["h"].lower(), "资料和名单里同一个 id 的用户名不一致（可能改过名，重新拉一次名单）",
                 "The same id has different usernames in the profiles and the list (maybe renamed: fetch the list again)")
            prof = {k: v for k, v in u.items() if k not in ("follows_you", "you_follow")}
            if f.get("profile"):
                need(f["profile"] == prof, "同一个账号的资料出现了两次而且内容不同", "One account's profile appears twice with different data")
            f["profile"] = prof

    timelines = {}
    for item in cfg.timeline or []:
        uid, _, path = item.partition("=")
        need(bool(ID_RE.match(uid)) and path, f"--timeline 要写成 账号id=文件：{item}", f"--timeline takes id=file: {item}")
        need(uid in follows, f"--timeline 的账号 {uid} 不在关注名单里", f"--timeline account {uid} is not in the following list")
        need(uid not in timelines, f"--timeline 的账号 {uid} 给了两次", f"--timeline account {uid} given twice")
        rec = take(path, "timeline", uid)
        need(rec["mtime"] >= min(times) - timedelta(hours=1), f"账号 {uid} 的时间线比名单早得多，是旧文件：重新拉",
             f"Account {uid}'s timeline is much older than the list: fetch it again")
        posts = parse_timeline(rec, uid)
        timelines[uid] = {"posts": posts, "at": iso(rec["mtime"])} if posts is not None else {"error": True, "at": iso(rec["mtime"])}

    need("following" in owner, "拿不到你资料页上的关注数：拉 me 时带 user.fields=public_metrics，或把你自己的用户名放进资料的第一批",
         "Your profile's following count is missing: request user.fields=public_metrics for me, or put your own username "
         "in the first profiles batch")
    x_following = owner["following"]
    tol = max(2, math.ceil(x_following * 0.005))
    complete = chain_done and abs(len(follows) - x_following) <= tol
    info = {"listed": len(follows), "x_following": x_following, "complete": complete, "pages": len(pages),
            "last_page_reached": chain_done, "fetched": iso(max(times))}
    return owner, follows, timelines, info, evidence


# ============================================================================
# Sorting
# ============================================================================

def merged(a):
    """The account's fields from the following list, with the profiles batches filling only what the list lacks.
    Where both have a value, the evidence of activity wins: the newer most recent post and the larger post count.
    If they disagree on privacy, protected becomes None, which sorts the account as not enough evidence."""
    lst, extra = {k: v for k, v in a.items() if k != "profile"}, a.get("profile") or {}
    p = {}
    for k in ("followers", "created_at"):
        if lst.get(k) is not None or extra.get(k) is not None:
            p[k] = lst[k] if lst.get(k) is not None else extra[k]
    ids = [x for x in (lst.get("last_id"), extra.get("last_id")) if x]
    if ids:
        p["last_id"] = max(ids, key=int)
    counts = [x for x in (lst.get("statuses"), extra.get("statuses")) if x is not None]
    if counts:
        p["statuses"] = max(counts)
    flags = {x for x in (lst.get("protected"), extra.get("protected")) if x is not None}
    if flags:
        p["protected"] = flags.pop() if len(flags) == 1 else None
        if p["protected"] is None:
            p["privacy_conflict"] = True
    return p


def activity(a, timeline, policy, as_of):
    """(tier, reason, facts) from activity alone."""
    p = merged(a)
    facts = {"statuses": p.get("statuses"), "last": None, "last_days": None, "basis": "profile"}
    if not p or ("statuses" not in p and "last_id" not in p and "protected" not in p):
        return "U", "no_profile", facts
    if p.get("privacy_conflict"):
        return "U", "privacy_conflict", facts
    if "protected" not in p or "statuses" not in p:   # unknown is not "public" and not "zero posts"
        return "U", "no_data", facts
    if p["protected"]:
        return "U", "private", facts
    statuses, last_id = p["statuses"], p.get("last_id")
    if statuses and not last_id:
        return "U", "no_posts_visible", facts
    if last_id:   # evidence of a post outweighs a count of 0
        last = snowflake_time(last_id)
        need(last is None or last <= as_of + timedelta(minutes=10), "有账号的最近一帖 id 推出来是未来的时间",
             "An account's most recent post id decodes to a future time")
        facts["last"] = iso(last)[:10] if last else "<2010-11"
        facts["last_days"] = (as_of - last).days if last else None
        if last is not None and facts["last_days"] < policy["inactive_days"]:
            return "KEEP", "quick_active", facts
    if timeline is None:
        return "U", "needs_timeline", facts
    facts["basis"] = "timeline"
    if timeline.get("error"):
        return "U", "timeline_unavailable", facts
    posts = timeline["posts"]
    newest = max((parse_time(x["at"]) for x in posts), default=None)
    seen = parse_time(timeline["at"]) or as_of
    if newest is not None and (seen - newest).days < policy["inactive_days"]:
        facts["last"], facts["last_days"] = iso(newest)[:10], (seen - newest).days
        return "KEEP", "active_timeline", facts   # any recent post keeps it, whatever else the files say
    if last_id and not any(x["id"] == last_id for x in posts):
        # the timeline lacks the very post the profile calls the latest: stale, filtered, or the post is gone
        return "U", "timeline_inconsistent", facts
    if newest is None:
        return ("T2", "zero_posts", facts) if statuses == 0 else ("U", "no_posts_visible", facts)
    facts["last"], facts["last_days"] = iso(newest)[:10], (seen - newest).days
    return "T2", "inactive_confirmed", facts


def quick_rows(feats, decisions, prof, policy):
    ids, handles = xt.protected_sets(prof)
    as_of = parse_time(feats["list"]["fetched"]) or utcnow()
    rows = []
    for a in feats["accounts"]:
        dec = decisions.get(a["id"])
        pre = None
        if dec:
            kind = dec.get("decision")
            if kind == "keep_forever" or (kind == "defer_until" and (dec.get("until") or "") > utcnow().date().isoformat()):
                pre = ("KEEP", "decided")
            elif kind in ("unfollowed", "manual_unfollow", "not_following", "verified_unfollowed"):
                listed, decided = parse_time(feats["list"].get("fetched")), parse_time(dec.get("at"))
                pre = ("KEEP", "refollow") if listed and decided and listed > decided else ("DONE", "done")
        if not pre and (a["id"] in ids or a["h"].lower() in handles):
            pre = ("KEEP", "protected")
        tier, reason, facts = (pre[0], pre[1], {}) if pre else activity(a, a.get("timeline"), policy, as_of)
        sub = None
        if tier == "T2":
            if a.get("follows_you") is True:
                sub, tier = "T2", "M"
            elif a.get("follows_you") is None:
                tier, reason = "U", "mutual_unknown"
        p = merged(a)
        rows.append({"tier": tier, "reason": reason, "sub": sub, "judge": "none", "id": a["id"], "h": a["h"],
                     "name": a.get("name", ""), "score": 0, "why": [qlab(reason, policy)],
                     "followers": p.get("followers") or 0, "f": facts, "j": None, "quick": True})
    order = {k: i for i, k in enumerate(xt.TIERS)}
    rows.sort(key=lambda r: (order.get(r["tier"], 9), -((r["f"] or {}).get("last_days") or 0), r["h"].lower()))
    counters = Counter()
    for r in rows:
        if r["tier"] in xt.CODE_PREFIX:
            counters[r["tier"]] += 1
            r["code"] = f"{xt.CODE_PREFIX[r['tier']]}{counters[r['tier']]:03d}"
        else:
            r["code"] = None
    return rows


# ============================================================================
# Commands
# ============================================================================

def evidence_digest(manifest):
    return hashlib.sha256(json.dumps(sorted([e["role"], e.get("key") or "", e["sha256"]] for e in manifest),
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def cmd_quick(ctx):
    cfg = ctx.cfg
    now = utcnow()
    try:
        owner, follows, timelines, info, evidence = collect(ctx, cfg, now)
        rd = ctx.od / "runs" / (now.strftime("%Y%m%d-%H%M%S") + "-quick")
        ev = rd / "evidence"
        ev.mkdir(parents=True, exist_ok=True)
        manifest, copied = [], set()
        for e in evidence:
            rec = e["rec"]
            if rec["name"] not in copied:
                (ev / rec["name"]).write_bytes(rec["bytes"])
                copied.add(rec["name"])
            if rec.get("via"):
                src = Path(rec["via"]["path"])
                if src.name not in copied:
                    shutil.copyfile(src, ev / src.name)
                    copied.add(src.name)
            manifest.append({"role": e["role"], "key": e["key"], "sha256": rec["sha256"], "bytes": len(rec["bytes"]),
                             "saved_at": iso(rec["mtime"]), "quota": rec["quota"],
                             "via": rec["via"]["sha256"] if rec.get("via") else None})
        write_json(rd / "evidence.json", {"files": manifest})
        accounts = []
        for uid, a in follows.items():
            acc = {k: v for k, v in a.items() if k != "profile"}
            if a.get("profile"):
                acc["profile"] = a["profile"]
            if uid in timelines:
                acc["timeline"] = timelines[uid]
            accounts.append(acc)
        feats = {"schema": xt.SCHEMA, "mode": MODE, "run": rd.name, "generated": iso(now),
                 "owner": {"id": owner["id"], "handle": owner["h"], "name": owner.get("name", ""),
                           "followers": owner.get("followers"), "following": owner.get("following"),
                           "statuses": owner.get("statuses"), "bio": ""},
                 "list": info, "evidence": evidence_digest(manifest), "accounts": accounts}
        write_json(rd / "features.json", feats)
        ctx.set_current_run(rd)
        with ctx.lock():
            prof = ctx.profile()
            if not (prof.get("owner") or {}).get("id"):
                prof.update({"schema": xt.SCHEMA, "owner": feats["owner"]})
                ctx.save_profile(prof)
        write_outputs(ctx, rd, feats)
    except QuickError as e:
        sys.exit(t(f"快速检查停下了：{e}", f"Quick check stopped: {e}"))


def write_outputs(ctx, rd, feats):
    rows = quick_rows(feats, xt.load_decisions(ctx.od), ctx.profile(), ctx.policy)
    warnings = []
    li = feats["list"]
    if not li["complete"]:
        why = (t("还没翻到最后一页", "the last page was not reached") if not li.get("last_page_reached")
               else t(f"拿到 {li['listed']:,} 个，资料页显示 {li['x_following']:,} 个，差得太多",
                      f"{li['listed']:,} listed but the profile shows {li['x_following']:,}"))
        warnings.append(t(f"关注名单不完整（{why}）：只出报告，不能生成取关计划",
                          f"The following list is incomplete ({why}): report only, no unfollow plan"))
    warnings.append(t("快速检查没核对你和他们的互动（「一年来往 3 次以上就留着」这条没用上），选取关名单时自己留意",
                      "The quick check did not look at your interactions (the 3-interactions keep rule was not applied); "
                      "mind it when you pick"))
    pending = [r for r in rows if r["reason"] == "needs_timeline"]
    out = [{k: v for k, v in r.items()} for r in rows]
    write_json(rd / "tiers.json", {"schema": xt.SCHEMA, "mode": MODE, "run": rd.name, "generated": iso(utcnow()),
                                   "owner": feats["owner"], "policy": ctx.policy, "warnings": warnings,
                                   "quick": {"complete": feats["list"]["complete"], "evidence": feats["evidence"],
                                             "fetched": feats["list"]["fetched"], "listed": feats["list"]["listed"],
                                             "x_following": feats["list"]["x_following"]},
                                   "rows": out})
    (rd / "needs-timeline.txt").write_text("".join(r["id"] + "\n" for r in pending), encoding="utf-8")
    md, csvp = write_reports(rd, feats, out, ctx.policy, warnings)
    summary = quick_summary(feats, out, ctx.policy, md, rd, warnings, len(pending))
    (rd / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)


def retier(ctx, rd, feats):
    """tiers on a quick run: sort again with the latest decisions and protected list."""
    try:
        write_outputs(ctx, rd, feats)
    except QuickError as e:
        sys.exit(t(f"快速检查停下了：{e}", f"Quick check stopped: {e}"))


def write_reports(rd, feats, rows, policy, warnings):
    csvp = rd / "tiers.csv"
    with csvp.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "tier", "reason", "sub_tier", "handle", "id", "name", "url", "post_count", "last_post",
                    "days_since", "basis", "follows_you"])
        acc = {a["id"]: a for a in feats["accounts"]}
        for r in rows:
            f = r["f"] or {}
            w.writerow([xt.esc_csv(x) for x in (
                r["code"] or "", r["tier"], r["reason"], r["sub"] or "", r["h"], r["id"], r["name"],
                f"https://x.com/{r['h']}", f.get("statuses", ""), f.get("last") or "", f.get("last_days", ""),
                f.get("basis", ""), acc.get(r["id"], {}).get("follows_you", ""))])
    md = rd / "report.md"
    lines = [t(f"# @{feats['owner']['handle']} 关注快速检查（{feats['generated'][:10]}）",
               f"# @{feats['owner']['handle']}: quick check of the following list ({feats['generated'][:10]})"), "",
             t("- 只看活跃度、是否回关、是否私密；内容类规则和互动保护都没查。",
               "- Activity, follow-back and privacy only; no content rules and no interaction protection."),
             t(f"- 关注 {feats['list']['listed']:,} 个（资料页显示 {feats['list']['x_following']:,}），全部数据见 `{csvp.name}`",
               f"- Following {feats['list']['listed']:,} (the profile shows {feats['list']['x_following']:,}); all data in `{csvp.name}`"),
             t("- 二级可以整级选择；互关只能逐个挑；留着和证据不足不会进取关名单。",
               "- Tier 2 can be picked whole; mutuals one by one; keep and not-enough-evidence never get unfollowed.")]
    lines += [f"- ⚠️ {w}" for w in warnings]
    lines.append("")
    for tier in ("T2", "M", "U"):
        items = [r for r in rows if r["tier"] == tier]
        lines += [f"## {tier_label(tier)}（{len(items)}）", ""]
        if not items:
            lines += [t("无", "None"), ""]
            continue
        lines += ["| " + " | ".join([t("编号", "Code"), t("账号", "Account"), t("原因", "Reason"), t("最近发帖", "Last post"),
                                    t("帖子数", "Posts")]) + " |", "|---|---|---|---|---|"]
        for r in items:
            f = r["f"] or {}
            reason = qlab(r["reason"], policy) + (f"（{tier_label(r['sub'])}）" if r["sub"] else "")
            lines.append(f"| {r['code']} | [@{r['h']}](https://x.com/{r['h']}) | {xt.esc_md(reason)} | {f.get('last') or '-'} | "
                         f"{f.get('statuses') if f.get('statuses') is not None else '-'} |")
        lines.append("")
    keep = Counter(r["reason"] for r in rows if r["tier"] == "KEEP")
    lines += [f"## {tier_label('KEEP')}（{sum(keep.values())}）", ""]
    lines += [f"- {qlab(k, policy)}：{v}" for k, v in keep.most_common()]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md, csvp


def quick_summary(feats, rows, policy, md, rd, warnings, pending):
    c = Counter(r["tier"] for r in rows)
    out = [t(f"@{feats['owner']['handle']} 关注 {feats['list']['listed']:,} 个，快速检查（只看活跃度，没看内容）：",
             f"@{feats['owner']['handle']} follows {feats['list']['listed']:,}. Quick check (activity only, no content):")]
    for tier in ("T2", "M"):
        items = [r for r in rows if r["tier"] == tier]
        why = Counter(r["reason"] for r in items).most_common(3)
        out.append(f"- {tier_label(tier)}：{len(items)}" + (
            "（" + "，".join(f"{qlab(k, policy)} {v}" for k, v in why) + "）" if why else ""))
    keep = Counter(r["reason"] for r in rows if r["tier"] == "KEEP")
    out.append(f"- {tier_label('KEEP')}：{c.get('KEEP', 0)}（" + "，".join(f"{qlab(k, policy)} {v}" for k, v in keep.most_common(3)) + "）")
    unc = Counter(r["reason"] for r in rows if r["tier"] == "U")
    out.append(f"- {tier_label('U')}：{c.get('U', 0)}" + (
        "（" + "，".join(f"{qlab(k, policy)} {v}" for k, v in unc.most_common(4)) + "）" if unc else ""))
    if c.get("DONE"):
        out.append(t(f"- 已取关或不在关注：{c['DONE']}", f"- Already unfollowed / not followed: {c['DONE']}"))
    if pending:
        out.append(t(f"下一步：{pending} 个看起来不活跃，要复查时间线。账号 id 在 {rd / 'needs-timeline.txt'}，"
                     f"对每个 id 调用 get_users_posts（post.fields 写 author_id,created_at,referenced_tweets；不排除回复和转发；"
                     f"max_results 5；打开 write_to_file），再带上这次的全部参数和 --timeline 111=文件 222=文件 … 重新运行 quick。",
                     f"Next: {pending} look inactive and need a timeline check. Their ids are in {rd / 'needs-timeline.txt'}; "
                     f"call get_users_posts for each (post.fields author_id,created_at,referenced_tweets; replies and reposts "
                     f"included; max_results 5; write_to_file on), then run quick again with this run's arguments plus "
                     f"--timeline 111=file 222=file …"))
    out += [f"⚠️ {w}" for w in warnings]
    out.append(t(f"完整名单：{md}", f"Full list: {md}"))
    return "\n".join(out)
