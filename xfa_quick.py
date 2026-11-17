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
HASH_NAME = re.compile(r"[0-9a-f]{64}\.txt")
ID_RE = re.compile(r"[0-9]{1,20}")
USER_RE = re.compile(r"[A-Za-z0-9_]{1,15}")
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
    need(bool(HASH_NAME.fullmatch(p.name)), f"{p.name}：文件名不是「内容的 SHA-256 + .txt」，只收 Bot 归档目录里的原始文件",
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
# Reading the list following.js saved in the browser (no X developer charges)
# ============================================================================

BROWSER_KIND = "xfa-following"
BROWSER_KEYS = ["kind", "v", "tool", "capture_id", "owner", "page", "started", "finished", "status", "end_signal",
                "network", "dom", "entries", "excluded", "digest"]
BROWSER_STATUS = {"done", "missed_first_page", "no_end_signal", "stalled", "empty", "timeout", "stopped", "paused",
                  "navigated"}
NET_KEYS = ["pages", "users", "heard", "unavailable", "malformed", "dupes", "errors", "unrecovered", "last_error", "other",
            "top_refreshes", "restarts", "stale", "chain_breaks", "first_heard", "end_seen", "last_users"]
DOM_KEYS = ["steps", "renders", "no_button", "bad_cell", "gaps", "hidden_waits", "nudges", "retries", "dom_only"]
EXCLUDED_WHY = {"conflict", "not_following", "unknown_follow"}
BROWSER_TOOL = re.compile(r"following\.js [0-9]+\.[0-9]+\.[0-9]+")
CAPTURE_RE = re.compile(r"[0-9a-f]{16}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
ISO_MS = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")   # Date.toISOString()
MAX_SAFE = 2 ** 53 - 1
EXPORT_MINUTES = 25            # following.js gives up after this long
EXPORT_TASK_MINUTES = 40       # on Grok Bot it must end this long after the task began, like a batch


def _count(v, nullable=False):
    if v is None:
        return nullable
    return type(v) is int and 0 <= v <= MAX_SAFE


def _ascii_only(o):
    """Every string in the parsed file, keys included, must be ASCII: an escape like \\u2028 passes the byte check
    but would make JSON.stringify and json.dumps write different bytes."""
    if isinstance(o, str):
        return o.isascii()
    if isinstance(o, dict):
        return all(_ascii_only(k) and _ascii_only(v) for k, v in o.items())
    if isinstance(o, list):
        return all(_ascii_only(x) for x in o)
    return o is None or isinstance(o, bool) or (type(o) is int and abs(o) <= MAX_SAFE)


def browser_digest(doc):
    """What following.js signs: the whole file except "digest", as JSON.stringify writes it. Every value in the file
    is ASCII, so Python's compact json.dumps gives the same bytes. It catches a file changed or cut after it was
    saved; it cannot tell a forged file from a real one."""
    rest = {k: v for k, v in doc.items() if k != "digest"}
    return hashlib.sha256(json.dumps(rest, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def read_browser_list(path):
    """The JSON file following.js downloaded → a record shaped like read_archive's (named by its SHA-256)."""
    p = Path(path)
    need(not p.is_symlink(), f"{p.name} 是软链接，请直接给下载下来的文件", f"{p.name} is a symlink; give the downloaded file itself")
    need(p.is_file(), f"找不到文件 {p}", f"No such file: {p}")
    need(0 < p.stat().st_size <= MAX_BYTES, f"{p.name} 是空的或太大", f"{p.name} is empty or too large")
    raw = p.read_bytes()
    need(raw.isascii(), f"{p.name} 里有非 ASCII 字符，不是 following.js 导出的原文件",
         f"{p.name} holds non-ASCII bytes; it is not the file following.js saved")
    try:
        obj = DECODER.decode(raw.decode("ascii"))
    except ValueError as e:
        raise QuickError(t(f"{p.name}：JSON 读不出来（{e}）", f"{p.name}: unreadable JSON ({e})"))
    need(isinstance(obj, dict), f"{p.name}：顶层不是对象", f"{p.name}: the top level is not an object")
    digest = hashlib.sha256(raw).hexdigest()
    return {"path": str(p), "name": f"{digest}.json", "label": p.name, "sha256": digest, "bytes": raw, "obj": obj,
            "quota": None, "mtime": None, "via": None}


def export_record(od, capture_id, with_path=False):
    """The record `xfa export` wrote for this capture id, or None."""
    for f in sorted((Path(od) / "exports").glob("E*.json")):
        rec = read_json(f, {})
        if isinstance(rec, dict) and rec.get("capture_id") == capture_id:
            return (rec, f) if with_path else rec
    return (None, None) if with_path else None


def parse_browser_list(rec, now, od=None, handle=None):
    """Check everything following.js wrote. With od and handle, the capture id must be one `xfa export` issued for
    this account, and the read must fall inside that export's time. Returns the facts the rest of the check uses;
    "read_ok" is true only for a read that ended with an end signal, every reply in order, no error and no gap."""
    o, name = rec["obj"], rec.get("label") or rec["name"]
    need(list(o) == BROWSER_KEYS and o.get("kind") == BROWSER_KIND and type(o.get("v")) is int and o["v"] == 1,
         f"{name}：不是 following.js 0.6 导出的关注名单", f"{name}: not a following list saved by following.js 0.6")
    need(_ascii_only(o), f"{name}：内容里有非 ASCII 字符或超范围的数字，不是 following.js 导出的原文件",
         f"{name}: it holds non-ASCII text or out-of-range numbers; it is not the file following.js saved")
    need(isinstance(o["tool"], str) and bool(BROWSER_TOOL.fullmatch(o["tool"])), f"{name}：tool 字段不对", f"{name}: bad tool field")
    need(isinstance(o["capture_id"], str) and bool(CAPTURE_RE.fullmatch(o["capture_id"])),
         f"{name}：capture_id 不对", f"{name}: bad capture_id")
    ow = o["owner"]
    need(isinstance(ow, dict) and list(ow) == ["id", "handle"], f"{name}：owner 字段不对", f"{name}: bad owner field")
    oid, oh = _id(ow["id"], "owner.id", name), _handle(ow["handle"], name)
    need(o["page"] == f"/{oh}/following", f"{name}：读的不是本人的关注页", f"{name}: the page read is not the owner's Following list")
    need(all(isinstance(o[k], str) and bool(ISO_MS.fullmatch(o[k])) for k in ("started", "finished")),
         f"{name}：时间格式不对（要 UTC 的 2026-01-01T00:00:00.000Z）", f"{name}: times must look like 2026-01-01T00:00:00.000Z")
    started, finished = parse_time(o["started"]), parse_time(o["finished"])
    need(started is not None and finished is not None and started <= finished,
         f"{name}：开始、结束时间不对", f"{name}: bad start or end time")
    need(finished - started <= timedelta(hours=1), f"{name}：读了一个多小时，名单前后可能不一致，重新导出",
         f"{name}: the read took over an hour, so the list may be inconsistent: save it again")
    need(finished <= now + timedelta(minutes=10), f"{name}：结束时间在未来", f"{name}: the end time is in the future")
    need(o["status"] in BROWSER_STATUS, f"{name}：状态 {o['status']!r} 认不出", f"{name}: unknown status {o['status']!r}")
    need(isinstance(o["end_signal"], bool), f"{name}：end_signal 不对", f"{name}: bad end_signal")
    net, dom = o["network"], o["dom"]
    need(isinstance(net, dict) and list(net) == NET_KEYS and isinstance(net["first_heard"], bool) and
         isinstance(net["end_seen"], bool) and
         all(_count(net[k], nullable=k in ("last_users", "last_error")) for k in NET_KEYS if k not in ("first_heard", "end_seen")),
         f"{name}：network 字段不对", f"{name}: bad network field")
    need(net["users"] == net["heard"] + net["unavailable"] + net["malformed"] + net["dupes"],
         f"{name}：听到的账号数对不上", f"{name}: the accounts heard do not add up")
    need(isinstance(dom, dict) and list(dom) == DOM_KEYS and all(_count(dom[k]) for k in DOM_KEYS),
         f"{name}：dom 字段不对", f"{name}: bad dom field")
    need(isinstance(o["entries"], list) and isinstance(o["excluded"], list), f"{name}：名单格式不对", f"{name}: malformed list")
    entries, ids, handles = [], set(), set()
    for e in o["entries"]:
        need(isinstance(e, dict) and list(e) == ["id", "h", "fy", "fc"] and (e["fy"] is None or isinstance(e["fy"], bool))
             and isinstance(e["fc"], bool) and not (e["fc"] and e["fy"] is not None),
             f"{name}：名单条目格式不对", f"{name}: malformed entry")
        uid, h = _id(e["id"], "id", name), _handle(e["h"], name)
        need(uid not in ids and h.lower() not in handles, f"{name}：同一个账号出现了两次", f"{name}: an account appears twice")
        need(uid != oid and h.lower() != oh.lower(), f"{name}：名单里出现了本人", f"{name}: the owner is in their own list")
        ids.add(uid)
        handles.add(h.lower())
        entries.append({"id": uid, "h": h, "fy": e["fy"], "fc": e["fc"]})
    excluded = Counter()
    seen_ex = set()
    for e in o["excluded"]:
        need(isinstance(e, dict) and list(e) == ["id", "why"] and e["why"] in EXCLUDED_WHY,
             f"{name}：排除条目格式不对", f"{name}: malformed excluded entry")
        xid = _id(e["id"], "id", name)
        need(xid not in ids and xid not in seen_ex, f"{name}：同一个账号既在名单里又被排除，或排除了两次",
             f"{name}: an account is both listed and excluded, or excluded twice")
        seen_ex.add(xid)
        excluded[e["why"]] += 1
    need(len(entries) + len(seen_ex) == net["heard"], f"{name}：听到的每个账号都要么在名单里、要么写明排除原因，数对不上",
         f"{name}: every account heard must be listed or excluded with a reason, and the numbers differ")
    need(isinstance(o["digest"], str) and bool(SHA_RE.fullmatch(o["digest"])) and browser_digest(o) == o["digest"],
         f"{name}：校验值对不上，文件导出以后被改过或不完整，重新导出",
         f"{name}: the digest does not match; the file was changed or cut after it was saved: save it again")
    # The page's own replies are the list: complete only if the first page was heard, every later reply asked for
    # the page due next, a failed page came back, the last page carried X's end mark (TimelineTerminateTimeline to
    # the bottom), every account heard is accounted for, and the page showed no followed account that no reply
    # brought. following.js says "done" only then; check it all again here.
    end = net["pages"] > 0 and net["end_seen"]
    need(not o["end_signal"] or end, f"{name}：结束信号和最后一次返回对不上", f"{name}: the end signal does not match the last reply")
    unexplained = sum(v for k, v in excluded.items() if k != "not_following")
    read_ok = (o["status"] == "done" and o["end_signal"] and net["first_heard"] and net["unrecovered"] == 0
               and net["chain_breaks"] == 0 and net["malformed"] == 0 and dom["dom_only"] == 0 and unexplained == 0
               and bool(entries))
    need(o["status"] != "done" or read_ok, f"{name}：状态是 done，但首页、结束信号、报错或漏听的记录对不上",
         f"{name}: status done does not agree with its first page, end signal, errors or unheard replies")
    need(handle is None or oh.lower() == str(handle).lower(),
         f"浏览器名单是 @{oh} 的关注，不是 @{handle}：浏览器登录的可能是别的账号",
         f"The browser list belongs to @{oh}, not @{handle}: the browser may be signed in to another account")
    if od is not None:
        ex = export_record(od, o["capture_id"])
        need(ex is not None, f"{name}：capture_id 不是 xfa export 为这个账号生成的，可能是旧文件或别处的文件",
             f"{name}: its capture_id was not issued by xfa export for this account (an old or foreign file)")
        need(str(ex.get("handle", "")).lower() == str(handle or "").lower() == oh.lower(),
             f"{name}：导出记录的账号对不上", f"{name}: the export record is for another account")
        need(not ex.get("owner_id") or str(ex["owner_id"]) == oid, f"{name}：导出记录的账号 id 对不上",
             f"{name}: the export record has another account id")
        need(not ex.get("bound_sha256") or ex["bound_sha256"] == rec["sha256"],
             f"{name}：这个导出编号已经用过另一份文件；同一次导出只认第一次导入的那份",
             f"{name}: this export id was already used with another file; an export accepts only the file first imported")
        created, deadline = parse_time(ex.get("created")), parse_time(ex.get("deadline"))
        need(created is not None and deadline is not None and started >= created - timedelta(minutes=2)
             and finished <= deadline + timedelta(minutes=2),
             f"{name}：读取时间不在这次导出的时间窗里", f"{name}: the read falls outside its export's time window")
    return {"owner": {"id": oid, "h": oh}, "started": started, "finished": finished, "status": o["status"],
            "read_ok": read_ok, "entries": entries, "excluded": dict(excluded), "tool": o["tool"],
            "capture_id": o["capture_id"], "network": dict(net), "dom": dict(dom)}


BROWSER_WHY = {
    "no_end_signal": ("没收到页面「到底了」的信号", "no end signal from the page"),
    "missed_first_page": ("没听到第一页：要在刚打开的 x.com/home 上运行", "the first page went unheard: run it on a freshly loaded x.com/home"),
    "stalled": ("页面报错、被限流，或漏听了一页", "the page errored, was rate limited, or a page went unheard"),
    "empty": ("一个账号也没读到", "no account was read"),
    "timeout": ("到截止时间还没读完", "time ran out"),
    "stopped": ("中途停下了", "it was stopped early"),
    "paused": ("电脑休眠过", "the computer slept"),
    "navigated": ("中途离开了关注页", "the page was left"),
}


def fmt_count(n):
    return f"{n:,}" if isinstance(n, int) and not isinstance(n, bool) else "?"


def profile_batches(feats, size=100):
    """Usernames still without a profile, up to 100 a line, the owner first while the owner is not confirmed."""
    names = [] if feats["list"].get("owner_verified") else [feats["owner"]["handle"]]
    names += [a["h"] for a in feats["accounts"] if not a.get("profile")]
    return [names[i:i + size] for i in range(0, len(names), size)]


def cmd_export(ctx):
    """Write a copy of following.js for one read of the owner's Following list: its capture id, the account and
    the deadline are filled in, and a record lets `quick --browser-list` accept only the file this copy saves."""
    cfg = ctx.cfg
    now = utcnow()
    tpl = Path(__file__).resolve().parent / "following.js"
    marker = "/*XFA_CONFIG*/null/*XFA_CONFIG_END*/"
    text = tpl.read_text(encoding="utf-8") if tpl.is_file() else ""
    if text.count(marker) != 1:
        sys.exit(t(f"找不到 following.js 模板（应在 {tpl}）：重新下载完整的代码",
                   f"following.js is missing or damaged (expected at {tpl}): download the code again"))
    deadline = now + timedelta(minutes=EXPORT_MINUTES)
    started = getattr(cfg, "task_start", None)
    if started:
        ts = parse_time(started)
        if not ts or ts > now + timedelta(minutes=2):
            sys.exit(t(f"--task-start {started!r} 不对：写这一轮任务开始的 UTC 时间，例如 2026-11-17T08:00:00Z",
                       f"--task-start {started!r} is wrong: give the UTC time this task began, e.g. 2026-11-17T08:00:00Z"))
        deadline = min(deadline, ts + timedelta(minutes=EXPORT_TASK_MINUTES))
    elif ctx.policy["runtime"] == "grok-bot":
        sys.exit(t("在 Grok Bot 上导出名单要带 --task-start（这一轮任务开始的 UTC 时间，可用 date -u +%Y-%m-%dT%H:%M:%SZ 取得）",
                   "On Grok Bot an export needs --task-start (the UTC time this task began, e.g. from date -u +%Y-%m-%dT%H:%M:%SZ)"))
    if deadline - now < timedelta(minutes=8):
        sys.exit(t("这一轮任务剩下的时间不够读完名单（至少要 8 分钟）：下一轮一开始就导出",
                   "Too little of this task is left to read the list (8 minutes at least): export at the start of the next task"))
    owner_id = (ctx.profile().get("owner") or {}).get("id")
    capture = os.urandom(8).hex()
    out = ctx.od / "exports"
    out.mkdir(parents=True, exist_ok=True)
    n = 1 + max([int(f.stem[1:]) for f in out.glob("E[0-9][0-9][0-9]*.*") if f.stem[1:].isdigit()] or [0])
    name = f"E{n:03d}"
    config = {"capture_id": capture, "handle": ctx.handle, "owner_id": str(owner_id) if owner_id else None,
              "deadline": int(deadline.timestamp() * 1000)}
    script = text.replace(marker, "/*XFA_CONFIG*/" + json.dumps(config) + "/*XFA_CONFIG_END*/")
    (out / f"{name}.js").write_text(script, encoding="utf-8")
    write_json(out / f"{name}.json", {"capture_id": capture, "handle": ctx.handle, "owner_id": config["owner_id"],
                                      "created": iso(now), "deadline": iso(deadline), "script": f"{name}.js",
                                      "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest()})
    left = int((deadline - now).total_seconds() // 60)
    print(t(f"导出脚本：{out / (name + '.js')}（只读，最多 {left} 分钟，截止 {iso(deadline)}）\n"
            f"1. 在云浏览器重新加载 https://x.com/home ，确认左侧主页链接是你自己的账号（不要先打开关注页）；\n"
            f"2. 在这个标签页的开发者工具控制台粘贴执行这份文件的全部内容，几秒后看 window.__xfaFollowingStart，"
            f"它会自己打开关注页往下滚；\n"
            f"3. 让标签页一直在前台，每 2 到 5 分钟执行一次 xfaFollowingResult() 看 status，直到不是 running；"
            f"列表底部出错时脚本会在 1 分钟后自己点「重试」（最多 5 次）；整页变成出错页时，等 1 分钟点一次页面上的「重试」，"
            f"点两次还出错就停下；\n"
            f"4. 下载的文件叫 xfa-following-{ctx.handle}-{capture}.json；然后运行 quick {ctx.handle} --browser-list <这个文件>。\n"
            f"读名单不花 X 开发者账号的钱；下载失败时用 copy(xfaFollowingText()) 取全文，不要改走付费插件。",
            f"Export script: {out / (name + '.js')} (read-only, at most {left} minutes, until {iso(deadline)})\n"
            f"1. Reload https://x.com/home in the cloud browser and check the side-nav profile link is your own account "
            f"(do not open the Following page first);\n"
            f"2. In that tab's DevTools console, paste and run the whole file, then after a few seconds read "
            f"window.__xfaFollowingStart; it opens the Following page and scrolls by itself;\n"
            f"3. Keep the tab in front; every 2 to 5 minutes run xfaFollowingResult() until status is no longer running; "
            f"if the list shows an error at its bottom the script presses Retry a minute later (5 times at most); if the "
            f"whole page turns into an error page, wait a minute and press its Retry once, and stop after two such presses;\n"
            f"4. The download is named xfa-following-{ctx.handle}-{capture}.json; then run quick {ctx.handle} "
            f"--browser-list <that file>.\n"
            f"Reading the list costs nothing on your X developer account; if the download fails, use "
            f"copy(xfaFollowingText()) for the text instead of switching to the paid plugin."))


# ============================================================================
# Parsing X API v2 results
# ============================================================================

def _top(rec, allowed=TOP_KEYS):
    o = rec["obj"]
    extra = set(o) - allowed
    need(not extra, f"{rec['name']}：多了认不出的顶层字段 {sorted(extra)}", f"{rec['name']}: unknown top-level keys {sorted(extra)}")
    return o


def _id(v, what, name):
    need(isinstance(v, str) and bool(ID_RE.fullmatch(v)), f"{name}：{what} 不是数字字符串", f"{name}: {what} is not a numeric string")
    return v


def _handle(v, name):
    need(isinstance(v, str) and bool(USER_RE.fullmatch(v)), f"{name}：用户名格式不对", f"{name}: malformed username")
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

    browser = bool(getattr(cfg, "browser_list", None))
    need(browser != bool(cfg.following), "关注名单只能给一种：--browser-list（浏览器导出的文件）或 --following（X 插件的各页）",
         "Give the following list one way only: --browser-list (the file the browser saved) or --following (the X plugin pages)")
    need(browser or cfg.me, "用 --following 时要给 --me（get_users_me 的归档文件）",
         "--following needs --me (the saved get_users_me result)")
    known = (ctx.profile().get("owner") or {}).get("id")
    me = None
    if cfg.me:
        me = parse_me(take(cfg.me, "me"))
        need(me["h"].lower() == ctx.handle.lower(), f"me 文件里的账号是 @{me['h']}，不是 @{ctx.handle}",
             f"The me file is @{me['h']}, not @{ctx.handle}")
    if browser:
        follows, times, chain_done, pages_n, bl = collect_browser(ctx, cfg, now, me, known, evidence)
        if me is None:
            me = {"id": bl["owner"]["id"], "h": bl["owner"]["h"], "name": ""}
    else:
        need(not known or str(known) == me["id"], "个人设置里记下的账号 id 和 me 文件不一致：确认 Bot 连接的是同一个 X 账号",
             "The account id in the profile differs from the me file: check the Bot's connected X account")
        follows, times, chain_done, pages_n = collect_pages(cfg, now, me, take)
        bl = None
    return collect_rest(ctx, cfg, now, me, follows, times, chain_done, pages_n, bl, take, evidence)


def collect_browser(ctx, cfg, now, me, known, evidence):
    """The list from following.js: the owner is whoever is signed in to the browser (twid cookie and profile link),
    and must be the account in the command, the saved profile and the me file, if one is given."""
    rec = read_browser_list(cfg.browser_list)
    bl = parse_browser_list(rec, now, ctx.od, ctx.handle)
    rec["mtime"] = bl["finished"]
    evidence.append({"role": "browser_list", "key": None, "rec": rec})
    oid, oh = bl["owner"]["id"], bl["owner"]["h"]
    need(oh.lower() == ctx.handle.lower(), f"浏览器名单是 @{oh} 的关注，不是 @{ctx.handle}：浏览器登录的可能是别的账号",
         f"The browser list belongs to @{oh}, not @{ctx.handle}: the browser may be signed in to another account")
    need(not known or str(known) == oid, "个人设置里记下的账号 id 和浏览器登录的账号不一致：确认浏览器登录的是你自己的账号",
         "The account id in the profile differs from the account signed in to the browser: check that it is your own")
    need(me is None or me["id"] == oid, "me 文件和浏览器登录的不是同一个账号", "The me file and the browser are different accounts")
    need(now - bl["finished"] <= timedelta(days=3), "名单是 3 天以前导出的，太旧了，重新导出一遍",
         "The list was saved more than 3 days ago: save it again")
    need(bl["entries"], "名单是空的：关注页没加载出来，打开关注页确认能看到名单，再导出一次",
         "The list is empty: the page did not load; open the Following page, check it shows the list, and save again")
    follows = {e["id"]: dict({"id": e["id"], "h": e["h"], "name": "", "follows_you": e["fy"], "you_follow": True},
                             **({"relation_conflict": True} if e["fc"] else {})) for e in bl["entries"]}
    return follows, [bl["started"], bl["finished"]], bl["read_ok"], None, bl


def collect_pages(cfg, now, me, take):
    """The list from the X plugin: pages in the order they were fetched, the first as a file, every later one as
    token=file, where token is the pagination_token the Bot sent for it. Each token must be the previous page's
    next_token, so a skipped, repeated or reordered page breaks the chain instead of passing as a shorter list."""
    pages = []
    for i, item in enumerate(cfg.following):
        token, path = (None, item) if i == 0 else tuple(item.split("=", 1)) if "=" in item else (None, item)
        need(i == 0 or token, f"--following 第 {i + 1} 项要写成 翻页token=文件（token 是请求这一页时用的 pagination_token）",
             f"--following item {i + 1} must be token=file (the pagination_token used to request that page)")
        rec = take(path, "following", token or "first")
        users, nt = parse_following_page(rec)
        pages.append({"token": token, "sha": rec["sha256"], "next": nt, "users": users, "mtime": captured_at(rec)})
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
    return follows, times, pages[-1]["next"] is None, len(pages)


def count_fits(listed, x_following, unavailable=0):
    """The list against the profile's following count: X counts suspended or deactivated accounts without listing
    them. Accounts the replies marked unavailable explain that much of a shortfall; the rest must stay within 0.5%
    (at least 2)."""
    gap = x_following - listed
    return -max(2, math.ceil(x_following * 0.005)) <= gap <= max(2, math.ceil(x_following * 0.005)) + unavailable


def captured_at(rec):
    """When a saved tool result was fetched, as late as the evidence allows: its modification time, but never later
    than the moment the read-quota sentence inside the file (which the file name hashes) says the per-minute window
    resets, at most a minute after the call. Copying a file makes its modification time new; the sentence stays."""
    q = rec.get("quota") or {}
    reset = parse_time(q.get("minute_resets")) if q else None
    return min(rec["mtime"], reset) if reset else rec["mtime"]


def collect_rest(ctx, cfg, now, me, follows, times, list_done, pages_n, bl, take, evidence):
    """Profiles, timelines and completeness: the same for the browser list and the plugin pages."""
    browser = bl is not None
    owner, owner_seen = dict(me), cfg.me is not None
    for path in cfg.profiles or []:
        rec = take(path, "profiles")
        at = captured_at(rec)
        need(min(times) - timedelta(hours=1) <= at <= max(times) + timedelta(hours=6),
             "资料文件和名单不是同一次拉的（相差超过几个小时），重新拉", "The profiles were not fetched with the list: fetch them again")
        need(at <= now + timedelta(minutes=10), "资料文件的时间在未来", "A profiles file is dated in the future")
        users, _errors = parse_profiles(rec)
        for u in users:
            if u["id"] == me["id"] or u["h"].lower() == me["h"].lower():
                need(u["id"] == me["id"] and u["h"].lower() == me["h"].lower(),
                     "资料里你自己的账号和名单的本人对不上：浏览器或插件连的可能是别的账号",
                     "Your own profile does not match the list's owner: the browser or the plugin may be another account")
                owner.update({k: v for k, v in u.items() if k not in ("follows_you", "you_follow")})
                owner_seen = True
                continue
            f = follows.get(u["id"])
            need(f is not None, "资料里有不在关注名单上的账号：分批时只能放名单里的用户名（和你自己）",
                 "The profiles hold an account that is not in the following list: batch only listed usernames (and your own)")
            need(f["h"].lower() == u["h"].lower(), "资料和名单里同一个 id 的用户名不一致（可能改过名，重新拿一次名单）",
                 "The same id has different usernames in the profiles and the list (maybe renamed: get the list again)")
            if browser:
                # the page says who follows whom; a profile that carries connection_status may fill in or disagree
                if u.get("you_follow") is False:
                    f["relation_conflict"] = True
                if "follows_you" in u:
                    if f.get("follows_you") is None:
                        f["follows_you"] = u["follows_you"]
                    elif f["follows_you"] != u["follows_you"]:
                        f["relation_conflict"] = True
            prof = {k: v for k, v in u.items() if k not in ("follows_you", "you_follow")}
            if f.get("profile"):
                need({k: v for k, v in f["profile"].items() if k != "at"} == prof,
                     "同一个账号的资料出现了两次而且内容不同", "One account's profile appears twice with different data")
            prof["at"] = iso(at)
            f["profile"] = prof

    timelines = {}
    for item in cfg.timeline or []:
        uid, _, path = item.partition("=")
        need(bool(ID_RE.fullmatch(uid)) and path, f"--timeline 要写成 账号id=文件：{item}", f"--timeline takes id=file: {item}")
        need(uid in follows, f"--timeline 的账号 {uid} 不在关注名单里", f"--timeline account {uid} is not in the following list")
        need(uid not in timelines, f"--timeline 的账号 {uid} 给了两次", f"--timeline account {uid} given twice")
        rec = take(path, "timeline", uid)
        at = captured_at(rec)
        need(at >= min(times) - timedelta(hours=1), f"账号 {uid} 的时间线比名单早得多，是旧文件：重新拉",
             f"Account {uid}'s timeline is much older than the list: fetch it again")
        need(at <= now + timedelta(minutes=10), f"账号 {uid} 的时间线时间在未来", f"Account {uid}'s timeline is dated in the future")
        posts = parse_timeline(rec, uid)
        timelines[uid] = {"posts": posts, "at": iso(at)} if posts is not None else {"error": True, "at": iso(at)}

    x_following = owner.get("following")
    need(x_following is not None or browser,
         "拿不到你资料页上的关注数：拉 me 时带 user.fields=public_metrics，或把你自己的用户名放进资料的第一批",
         "Your profile's following count is missing: request user.fields=public_metrics for me, or put your own username "
         "in the first profiles batch")
    unavailable = bl["network"]["unavailable"] if browser else 0
    count_ok = x_following is not None and count_fits(len(follows), x_following, unavailable)
    info = {"source": "browser" if browser else "plugin", "listed": len(follows), "x_following": x_following,
            "complete": bool(list_done and owner_seen and count_ok), "pages": pages_n, "last_page_reached": list_done,
            "owner_verified": owner_seen, "fetched": iso(max(times))}
    if browser:
        info.update({"status": bl["status"], "capture_id": bl["capture_id"], "unavailable": bl["network"]["unavailable"],
                     "excluded": bl["excluded"], "tool": bl["tool"]})
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
        seen = max(as_of, parse_time((a.get("profile") or {}).get("at")) or as_of)
        need(last is None or last <= seen + timedelta(minutes=10), "有账号的最近一帖 id 推出来晚于取数时间",
             "An account's most recent post id decodes to a time after its data was fetched")
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
        if tier == "T2" and a.get("relation_conflict"):
            tier, reason = "U", "relation_conflict"
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
    """Role, key, content hash and fetch time of every evidence file: the plan binds this, so none of them can be
    swapped or re-dated afterwards."""
    return hashlib.sha256(json.dumps(sorted([e["role"], e.get("key") or "", e["sha256"], e.get("saved_at") or "",
                                             e.get("via") or ""] for e in manifest),
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def recheck_run(ctx, rd, expect_evidence, expect_fetched=None, now=None):
    """Before plan and approve: the run's evidence files are all there and unchanged and still add up to the
    evidence digest on record; a browser list is read again from its copy and must still be complete against the
    owner's own profile in the evidence. Returns why not, or None. It does not take tiers.json's word for it."""
    now = now or utcnow()
    try:
        man = read_json(rd / "evidence.json", {}) or {}
        files = man.get("files") if isinstance(man, dict) else None
        need(isinstance(files, list) and files, "这次快速检查的证据清单不见了：重新跑 quick", "The evidence list of this quick check is missing: run quick again")
        for f in files:
            for sha in ([f.get("sha256"), f.get("via")] if isinstance(f, dict) else [None]):
                if sha is None and isinstance(f, dict) and f.get("sha256"):
                    continue   # no short file in front of this one
                need(isinstance(sha, str) and bool(SHA_RE.fullmatch(sha)), "证据清单格式不对", "Malformed evidence list")
                copy = next((c for c in (rd / "evidence" / f"{sha}.txt", rd / "evidence" / f"{sha}.json") if c.is_file()), None)
                need(copy is not None and hashlib.sha256(copy.read_bytes()).hexdigest() == sha,
                     f"证据文件 {sha[:12]} 不见了或被改过：重新跑 quick", f"Evidence file {sha[:12]} is missing or changed: run quick again")
        need(bool(expect_evidence) and evidence_digest(files) == expect_evidence,
             "证据和记录里的摘要对不上：重新跑 quick", "The evidence does not add up to the digest on record: run quick again")
        lists = [f for f in files if f.get("role") == "browser_list"]
        pages = [f for f in files if f.get("role") == "following"]
        need(bool(lists) != bool(pages), "证据里的名单来源不对", "The evidence holds no list, or both kinds")
        # the data time comes from the evidence: the browser list's own end time, or when the plugin pages were saved
        if lists:
            data_at = parse_time(read_browser_list(rd / "evidence" / f"{lists[0]['sha256']}.json")["obj"].get("finished"))
        else:
            data_at = max((parse_time(f.get("saved_at")) for f in pages), default=None)
        need(data_at is not None and data_at <= now + timedelta(minutes=10)
             and now - data_at <= timedelta(days=xt.QUICK_MAX_AGE_DAYS),
             f"快速检查的数据超过 {xt.QUICK_MAX_AGE_DAYS} 天或时间不对：重新取数、重新跑 quick",
             f"The quick check's data is older than {xt.QUICK_MAX_AGE_DAYS} days or badly dated: fetch again and rerun quick")
        fetched = parse_time(expect_fetched) if expect_fetched else None
        need(fetched is None or abs((fetched - data_at).total_seconds()) <= 2,
             "记录里的取数时间和证据对不上：重新跑 quick", "The fetch time on record does not match the evidence: run quick again")
        if lists:
            need(len(lists) == 1, "证据里有两份浏览器名单", "The evidence holds two browser lists")
            bl = parse_browser_list(read_browser_list(rd / "evidence" / f"{lists[0]['sha256']}.json"), now, ctx.od, ctx.handle)
            need(bl["read_ok"], "浏览器名单没有完整读到底：重新导出", "The browser list was not read to the end: export it again")
            following = None
            for f in files:
                rec = None
                if f.get("role") in ("profiles", "me"):
                    rec = read_archive(rd / "evidence" / f"{f['sha256']}.txt")
                users = (parse_profiles(rec)[0] if f.get("role") == "profiles" else [parse_me(rec)]) if rec else []
                for u in users:
                    if u["id"] == bl["owner"]["id"] and "following" in u:
                        following = u["following"]
            need(following is not None, "证据里没有你自己的资料，核对不了关注数", "Your own profile is not in the evidence")
            need(count_fits(len(bl["entries"]), following, bl["network"]["unavailable"]),
                 "名单条数和你资料上的关注数差得太多", "The list and your profile's following count differ too much")
    except QuickError as e:
        return str(e)
    return None


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
                             "saved_at": iso(captured_at(rec)), "quota": rec["quota"],
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
        if info.get("source") == "browser":
            ex, exf = export_record(ctx.od, info["capture_id"], with_path=True)
            sha = next(m["sha256"] for m in manifest if m["role"] == "browser_list")
            if ex is not None and not ex.get("bound_sha256"):
                ex["bound_sha256"] = sha
                write_json(exf, ex)
        ctx.set_current_run(rd)
        with ctx.lock():
            prof = ctx.profile()
            if not (prof.get("owner") or {}).get("id") and info.get("owner_verified"):
                prof.update({"schema": xt.SCHEMA, "owner": feats["owner"]})
                ctx.save_profile(prof)
        write_outputs(ctx, rd, feats)
    except QuickError as e:
        sys.exit(t(f"快速检查停下了：{e}", f"Quick check stopped: {e}"))


def write_outputs(ctx, rd, feats):
    rows = quick_rows(feats, xt.load_decisions(ctx.od), ctx.profile(), ctx.policy)
    warnings = []
    li = feats["list"]
    browser = li.get("source") == "browser"
    if not li["complete"]:
        if browser and not li.get("last_page_reached"):
            why = t(f"浏览器没把名单完整读到底（{BROWSER_WHY.get(li.get('status'), (li.get('status'),) * 2)[0]}）",
                    f"the browser did not read the whole list ({BROWSER_WHY.get(li.get('status'), (li.get('status'),) * 2)[1]})")
        elif not li.get("last_page_reached"):
            why = t("还没翻到最后一页", "the last page was not reached")
        elif not li.get("owner_verified") or li.get("x_following") is None:
            why = t("还没拉你自己的资料，核对不了账号和关注数", "your own profile is not fetched yet, so the account and count are unchecked")
        else:
            why = t(f"拿到 {li['listed']:,} 个，资料页显示 {li['x_following']:,} 个，差得太多",
                    f"{li['listed']:,} listed but the profile shows {li['x_following']:,}")
        warnings.append(t(f"关注名单不完整（{why}）：只出报告，不能生成取关计划",
                          f"The following list is incomplete ({why}): report only, no unfollow plan"))
    elif li.get("x_following") is not None and li["listed"] != li["x_following"]:
        un = li.get("unavailable") or 0
        warnings.append(t(f"名单 {li['listed']:,} 个，资料页显示关注 {li['x_following']:,} 个。"
                          + (f"X 在名单里标了 {un} 个不可用的账号（被封或停用），这部分有解释；" if un else "")
                          + "其余的差额原因不明（在 0.5% 以内才放行），留意一下",
                          f"{li['listed']:,} listed, the profile shows {li['x_following']:,}. "
                          + (f"X marked {un} accounts in the list as unavailable (suspended or deactivated), which explains "
                             f"that much; " if un else "")
                          + "the rest of the difference is unexplained (allowed only within 0.5%)"))
    if browser:
        warnings.append(t("名单来自浏览器里的关注页；是否回关以页面接口的明确字段为准，没有明确答案的算「不确定」，二级候选里的这种不会进计划",
                          "The list comes from the Following page in the browser; follow-back is taken from the page's own "
                          "replies where explicit, otherwise counted as unknown, and unknown tier-2 accounts stay out of plans"))
    warnings.append(t("快速检查没核对你和他们的互动（「一年来往 3 次以上就留着」这条没用上），选取关名单时自己留意",
                      "The quick check did not look at your interactions (the 3-interactions keep rule was not applied); "
                      "mind it when you pick"))
    pending = [r for r in rows if r["reason"] == "needs_timeline"]
    batches = profile_batches(feats) if browser else []
    out = [{k: v for k, v in r.items()} for r in rows]
    write_json(rd / "tiers.json", {"schema": xt.SCHEMA, "mode": MODE, "run": rd.name, "generated": iso(utcnow()),
                                   "owner": feats["owner"], "policy": ctx.policy, "warnings": warnings,
                                   "quick": {"complete": feats["list"]["complete"], "evidence": feats["evidence"],
                                             "fetched": feats["list"]["fetched"], "listed": feats["list"]["listed"],
                                             "x_following": feats["list"]["x_following"],
                                             "source": li.get("source", "plugin")},
                                   "rows": out})
    (rd / "needs-timeline.txt").write_text("".join(r["id"] + "\n" for r in pending), encoding="utf-8")
    (rd / "needs-profiles.txt").write_text("".join(",".join(b) + "\n" for b in batches), encoding="utf-8")
    md, csvp = write_reports(rd, feats, out, ctx.policy, warnings)
    summary = quick_summary(feats, out, ctx.policy, md, rd, warnings, len(pending), len(batches))
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
             t(f"- 关注 {feats['list']['listed']:,} 个（资料页显示 {fmt_count(feats['list']['x_following'])}），全部数据见 `{csvp.name}`",
               f"- Following {feats['list']['listed']:,} (the profile shows {fmt_count(feats['list']['x_following'])}); all data in `{csvp.name}`"),
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


def quick_summary(feats, rows, policy, md, rd, warnings, pending, profile_lines=0):
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
    if profile_lines:
        out.append(t(f"下一步：拉资料。{rd / 'needs-profiles.txt'} 有 {profile_lines} 行，每行调用一次自带的 get_users_by_usernames"
                     f"（usernames 就是这一行的用户名；user.fields 写 connection_status,created_at,protected,public_metrics,"
                     f"most_recent_tweet_id；打开 write_to_file），再带上这次的全部参数和 --profiles 文件1 文件2 … 重新运行 quick。"
                     f"每次调用前看上一次返回的剩余额度，不知道剩多少就先停。",
                     f"Next: fetch the profiles. {rd / 'needs-profiles.txt'} has {profile_lines} lines; call the built-in "
                     f"get_users_by_usernames once per line (usernames = that line; user.fields connection_status,created_at,"
                     f"protected,public_metrics,most_recent_tweet_id; write_to_file on), then run quick again with this run's "
                     f"arguments plus --profiles file1 file2 …. Check the quota left in the last result before each call; "
                     f"stop when it is unknown."))
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
