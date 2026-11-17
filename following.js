/* x-following-audit following.js 0.6.0 (template: run the copy `xfa export` writes, which fills in CONFIG)
 *
 * Reads YOUR OWN Following list on x.com in the browser you are signed in to and saves it as a JSON file for
 * `xfa quick <you> --browser-list <file>`. No API key, no X developer charges.
 *
 * Read-only: it opens your Following list inside the page (as a link would), scrolls it, and listens to the list
 * requests the page makes by itself. The one thing it ever clicks is the list's own "Retry" when a list request
 * failed and the list shows its error (after a minute, at most 5 times), which only loads the list again. It never sends a request of its own, never
 * follows or unfollows anyone, and keeps no cookies or request headers. Per account it keeps only the id, the
 * username and whether that account follows you (as X's own reply states it).
 *
 * Start it on a freshly loaded x.com/home (not on a Following page): it must hear the list's first page. A list
 * counts as read to the end only when the first page and every later one were heard in order, a failed page came
 * back on a retry, the last page carried X's own end mark (a TimelineTerminateTimeline instruction pointing to
 * the bottom), every account heard is accounted for, and the page showed no followed account the replies did not.
 * Otherwise the file says why, and `xfa quick` gives a report but no plan.
 *
 * In the DevTools console, after pasting:
 *   xfaFollowingResult()   short status as JSON text; "status" is "running" until it ends
 *   xfaFollowingStop()     stop early; what was read so far is saved, marked "stopped"
 *   xfaFollowingText()     the whole file as text, if the download did not arrive: copy(xfaFollowingText())
 *
 * Keep the tab in front while it runs: a hidden tab does not load more of the list, so the script only waits. If
 * the list starts over from its first page (as a Retry may do), the read starts over too: the list is rebuilt from
 * the new first page to the end mark, and what the page showed before must all come back.
 */
(() => {
  'use strict';
  const CONFIG = /*XFA_CONFIG*/null/*XFA_CONFIG_END*/;
  const VERSION = '0.6.0';
  const MAX_MINUTES = 25;        // never longer than this, whatever the deadline says
  const STEP = 0.8;              // scroll by this share of the window: under 1, so every entry passes through view
  const PAUSE_MS = 700;          // between scroll steps
  const BOTTOM_WAIT_MS = 2500;   // at the bottom, wait this long for more entries ...
  const END_CHECKS = 6;          // ... this many times in a row with nothing new: the page stopped loading
  const RETRY_WAIT_MS = 600000;  // after a failed page, wait this long for a retry before giving up
  const LATE_MS = 60000;         // a wait that wakes this much late means the computer slept: stop
  const RETRY_AFTER_MS = 60000;  // X's own Retry is pressed this long after the error shows ...
  const MAX_RETRIES = 5;         // ... at most this many times in one read
  const RETRY_TEXT = /^(retry|try again|重试|重試|再試行|다시 시도|réessayer|reintentar|erneut versuchen|riprova|tentar novamente|повторить)$/i;
  const HANDLE = /^[A-Za-z0-9_]{1,15}$/;
  const AT_HANDLE = /^@[A-Za-z0-9_]{1,15}$/;
  const ID = /^[0-9]{1,20}$/;
  const LIST_REQUEST = /\/graphql\/[A-Za-z0-9_-]+\/Following\?/;

  const say = m => console.log('[xfa] ' + m);
  if (!CONFIG || typeof CONFIG !== 'object' || !HANDLE.test(String(CONFIG.handle || '')) ||
      !/^[0-9a-f]{16}$/.test(String(CONFIG.capture_id || '')) || typeof CONFIG.deadline !== 'number') {
    window.__xfaFollowingStart = 'refused: run the file `xfa export` wrote, not this template';
    say(window.__xfaFollowingStart);
    return;
  }
  const old = window.__xfaFollowing;
  if (old && (old.status === 'running' || old.status === 'finishing')) {
    say('already running here; xfaFollowingResult() shows its progress');
    return;
  }

  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => Array.from(r.querySelectorAll(s));
  const same = (a, b) => !!a && !!b && String(a).toLowerCase() === String(b).toLowerCase();
  const twid = () => {
    const m = document.cookie.match(/(?:^|;\s*)twid=u%3D([0-9]{1,20})(?:;|$)/);
    return m ? m[1] : null;
  };
  const navHandle = () => {
    const a = q('[data-testid="AppTabBar_Profile_Link"]');
    const h = a ? (a.getAttribute('href') || '').replace(/^\//, '') : '';
    return HANDLE.test(h) ? h : null;
  };
  const pageHandle = () => {
    const m = location.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/following\/?$/);
    return m ? m[1] : null;
  };
  const switcherOk = () => {
    const hs = qa('[data-testid="SideNav_AccountSwitcher_Button"] span')
      .map(s => (s.textContent || '').trim()).filter(t => AT_HANDLE.test(t)).map(t => t.slice(1));
    return !hs.length || hs.some(h => same(h, CONFIG.handle));
  };
  // the account signed in when the script started is the only one it reads for, whatever the config knew
  const owner = { id: twid(), handle: navHandle() };
  const whyNotOwner = () => {
    const id = twid();
    if (!id) return 'not signed in to X here (no twid cookie)';
    if (id !== owner.id || (CONFIG.owner_id && id !== String(CONFIG.owner_id))) return 'signed in as another account (account id differs)';
    if (!same(navHandle(), CONFIG.handle)) return 'the side bar shows another account, or no profile link (make the window wider)';
    if (!switcherOk()) return 'the account switcher shows another account';
    return null;
  };

  const S = window.__xfaFollowing = {
    status: 'running', started: new Date().toISOString(), finished: null, file: null, digest: null, text: null,
    downloaded: null, stop: false, late: false,
    dom: new Map(),            // id -> {h, follow: 'unfollow'|'follow'|..., badge: bool, conflict: bool}
    net: new Map(),            // id -> {h, following: bool|null, followed_by: bool|null, conflict: bool}
    stats: { steps: 0, renders: 0, no_button: 0, bad_cell: 0, gaps: 0, hidden_waits: 0, nudges: 0, retries: 0, dom_only: 0 },
    netStats: { pages: 0, users: 0, heard: 0, unavailable: 0, malformed: 0, dupes: 0, errors: 0, unrecovered: 0,
                last_error: null, other: 0, top_refreshes: 0, restarts: 0, stale: 0, chain_breaks: 0, first_heard: false,
                end_seen: false, last_users: null },
    round: 0,                  // a new round starts each time the page asks for the first page again
    expect: '',                // the page the next request must ask for: '' is the first page, then the bottom
                               // cursor of the last good page
    consumed: new Set(),       // cursors of pages that came back fine: asking for one again is a loop
    unavail: new Set(),        // ids X marked unavailable (suspended, withheld), each counted once
    tops: new Set(), pending: false, failedAt: null, lastRetry: 0,
  };

  const counts = () => {
    let n = 0, fy = 0;
    for (const [id, x] of S.net) {
      const d = S.dom.get(id);
      if (x.following === false || (d && d.follow !== 'unfollow')) continue;
      n++;
      if (x.followed_by === true || (d && d.badge)) fy++;
    }
    return { n, fy };
  };
  window.xfaFollowingResult = () => {
    const c = counts(), N = S.netStats;
    return JSON.stringify({
      status: S.status, accounts: c.n, follows_you: c.fy, pages_heard: N.pages, errors: N.errors,
      unrecovered: N.unrecovered, last_error: N.last_error, steps: S.stats.steps, started: S.started,
      finished: S.finished, file: S.file, downloaded: S.downloaded, digest: S.digest ? S.digest.slice(0, 12) : null,
    });
  };
  window.xfaFollowingStop = () => { S.stop = true; return 'stopping'; };
  window.xfaFollowingText = () => S.text || '';

  // ---- the page's own list requests: listen only ----------------------------------------------------------------
  const walkEntries = (o, out) => {
    if (!o || typeof o !== 'object') return;
    if (Array.isArray(o)) { for (const x of o) walkEntries(x, out); return; }
    if (typeof o.entryId === 'string') { out.push(o); return; }
    for (const k of Object.keys(o)) walkEntries(o[k], out);
  };
  const bool = v => (typeof v === 'boolean' ? v : null);
  const addNet = (r, entryId) => {
    const N = S.netStats;
    const eid = (String(entryId || '').match(/^user-([0-9]{1,20})$/) || [])[1] || null;
    if (r && r.__typename === 'UserUnavailable') {   // suspended or withheld: X says so, once per account
      if (!eid) { N.malformed++; return; }             // no way to tell which account: it cannot explain anything
      if (S.unavail.has(eid) || S.net.has(eid)) { N.dupes++; return; }
      S.unavail.add(eid);
      N.unavailable = S.unavail.size;
      return;
    }
    const id = r && r.__typename === 'User' ? String(r.rest_id || '') : '';
    const h = r && ((r.core && r.core.screen_name) || (r.legacy && r.legacy.screen_name));
    if (!ID.test(id) || !HANDLE.test(String(h || '')) || (eid && eid !== id)) { N.malformed++; return; }
    if (S.unavail.delete(id)) { N.unavailable = S.unavail.size; N.dupes++; }   // listed as unavailable before: not both
    const rel = r.relationship_perspectives || {};
    const leg = r.legacy || {};
    const rec = {
      h, conflict: false,
      following: bool(rel.following) !== null ? rel.following : bool(leg.following),
      followed_by: bool(rel.followed_by) !== null ? rel.followed_by : bool(leg.followed_by),
    };
    const prev = S.net.get(id);
    if (prev) {
      N.dupes++;
      if (!same(prev.h, rec.h) || (prev.following !== null && rec.following !== null && prev.following !== rec.following) ||
          (prev.followed_by !== null && rec.followed_by !== null && prev.followed_by !== rec.followed_by)) prev.conflict = true;
      return;
    }
    S.net.set(id, rec);
    N.heard = S.net.size;
  };
  // A page of the list: an instructions array with entries to add, or X's mark that nothing more follows below.
  // Anything else (an error, {}, an empty array, a changed layout) is not a page and counts as a failure.
  const pageOf = b => {
    const t = b && b.data && b.data.user && b.data.user.result && b.data.user.result.timeline;
    const ins = t && t.timeline && t.timeline.instructions;
    if (!Array.isArray(ins)) return null;
    const adds = ins.filter(i => i && i.type === 'TimelineAddEntries' && Array.isArray(i.entries));
    const end = ins.some(i => i && i.type === 'TimelineTerminateTimeline' && String(i.direction || '').toLowerCase() === 'bottom');
    if (!adds.length && !end) return null;
    return { entries: [].concat(...adds.map(i => i.entries)), end };
  };
  const varsOf = url => {
    try { return JSON.parse(new URL(url, location.origin).searchParams.get('variables') || 'null'); } catch (_) { return null; }
  };
  // Called as the page SENDS a list request, so each reply is tied to the round it was asked for. A request for the
  // first page after the first round opens a new round (a Retry after a page-wide error can do that): everything
  // heard so far is dropped and the list is rebuilt from the new first page alone. What the page showed stays as
  // the cross-check, so accounts the new round never brings back keep the read from counting as complete.
  const roundFor = url => {
    const v = varsOf(url);
    if (S.status === 'running' && v && String(v.userId) === String(owner.id) && typeof v.cursor !== 'string') {
      if (S.round > 0) {
        const N = S.netStats;
        N.restarts++;
        S.consumed.clear();
        S.expect = '';
        S.pending = false;
        S.net.clear();
        S.unavail.clear();
        Object.assign(N, { pages: 0, users: 0, heard: 0, unavailable: 0, malformed: 0, dupes: 0, unrecovered: 0,
                           chain_breaks: 0, first_heard: false, end_seen: false, last_users: null });
      }
      S.round++;
    }
    return S.round;
  };
  const onReply = (url, status, body, round) => {
    if (S.status !== 'running') return;                  // nothing counts after the read ended
    const N = S.netStats;
    const vars = varsOf(url);
    if (!vars || String(vars.userId) !== String(owner.id)) { N.other++; return; }   // another account's list
    if (round !== S.round) { N.stale++; return; }        // a late reply to a request from an earlier round
    const cursor = typeof vars.cursor === 'string' ? vars.cursor : null;
    if (cursor && S.tops.has(cursor)) { N.top_refreshes++; return; }   // the page checking the top for new follows
    const key = cursor || '';
    // Order: every request must ask for the page due next, failed or not. A skipped page, a page asked for twice,
    // or a loop back to a page already read breaks the chain, and a broken chain is never complete.
    const due = key === S.expect && !S.consumed.has(key);
    if (!due) N.chain_breaks++;
    const page = status === 200 && body && typeof body === 'object' && !body.errors ? pageOf(body) : null;
    if (!page) {
      // a failed page counts until the same page comes back fine (the page's own Retry asks for it again)
      N.errors++;
      N.last_error = Number(status) || 0;
      if (due) { S.pending = true; S.failedAt = Date.now(); }
      N.unrecovered = S.pending ? 1 : 0;
      return;
    }
    if (due) {
      if (!key) N.first_heard = true;
      S.pending = false;
      N.unrecovered = 0;
      S.consumed.add(key);
    }
    let users = 0, bottom = null;
    for (const e of page.entries) {
      const value = e.content && typeof e.content.value === 'string' ? e.content.value : null;
      if (/^user-/.test(e.entryId)) {
        users++;
        const ic = e.content && e.content.itemContent;
        addNet(ic && ic.user_results && ic.user_results.result, e.entryId);
      } else if (/^cursor-bottom-/.test(e.entryId)) {
        bottom = value;
      } else if (/^cursor-top-/.test(e.entryId) && value) {
        S.tops.add(value);
      }
    }
    N.pages++;
    N.users += users;
    N.last_users = users;
    N.end_seen = page.end;
    if (due) S.expect = bottom === null ? '\u0000no-next-page' : bottom;   // nothing may come after a page without one
  };
  const hooks = { open: XMLHttpRequest.prototype.open, send: XMLHttpRequest.prototype.send, fetch: window.fetch };
  function wrapOpen(method, url) {
    try { this.xfaListUrl = LIST_REQUEST.test(String(url)) ? String(url) : null; } catch (_) { this.xfaListUrl = null; }
    return hooks.open.apply(this, arguments);
  }
  function wrapSend() {
    if (this.xfaListUrl) {
      this.xfaListRound = roundFor(this.xfaListUrl);
      if (!this.xfaListening) {   // one listener per request object, even if the page reuses it
        this.xfaListening = true;
        this.addEventListener('loadend', () => {
          const url = this.xfaListUrl;
          this.xfaListUrl = null;
          if (!url) return;
          let body = null;
          try {
            body = this.responseType === 'json' ? this.response
              : (this.responseType === '' || this.responseType === 'text') ? JSON.parse(this.responseText) : null;
          } catch (_) { body = null; }
          onReply(url, this.status, body, this.xfaListRound);
        });
      }
    }
    return hooks.send.apply(this, arguments);
  }
  function wrapFetch(input) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const listed = LIST_REQUEST.test(String(url));
    const round = listed ? roundFor(url) : 0;
    const p = hooks.fetch.apply(window, arguments);
    if (listed) {
      p.then(r => r.clone().json().then(b => onReply(url, r.status, b, round), () => onReply(url, r.status, null, round)),
             () => onReply(url, 0, null, round));
    }
    return p;
  }
  const hook = () => {
    XMLHttpRequest.prototype.open = wrapOpen;
    XMLHttpRequest.prototype.send = wrapSend;
    if (typeof hooks.fetch === 'function') window.fetch = wrapFetch;
  };
  const unhook = () => {   // put back only what is still ours
    if (XMLHttpRequest.prototype.open === wrapOpen) XMLHttpRequest.prototype.open = hooks.open;
    if (XMLHttpRequest.prototype.send === wrapSend) XMLHttpRequest.prototype.send = hooks.send;
    if (window.fetch === wrapFetch) window.fetch = hooks.fetch;
  };

  // ---- what the page shows -------------------------------------------------------------------------------------
  const listCells = () => {
    const col = q('[data-testid="primaryColumn"]');
    return col ? qa('[data-testid="UserCell"]', col) : [];
  };
  const readCell = c => {
    const btn = qa('[data-testid]', c).find(e => /^[0-9]{1,20}-[a-z]+$/.test(e.getAttribute('data-testid')));
    if (!btn) { S.stats.no_button++; return null; }
    const [id, follow] = btn.getAttribute('data-testid').split('-');
    const ats = qa('span', c).map(s => (s.textContent || '').trim()).filter(t => AT_HANDLE.test(t)).map(t => t.slice(1));
    const h = ats[0];
    const linked = h && qa('a[href]', c).some(a => same((a.getAttribute('href') || '').replace(/^\//, ''), h));
    const avatars = qa('[data-testid^="UserAvatar-Container-"]', c).map(e => e.getAttribute('data-testid').slice(21));
    if (!h || !linked || (avatars.length && !avatars.some(a => same(a, h)))) { S.stats.bad_cell++; return null; }
    return { id, h, follow, badge: !!q('[data-testid="userFollowIndicator"]', c) };
  };
  const collect = () => {
    const ids = new Set();
    if (S.status !== 'running' && S.status !== 'finishing') return ids;
    for (const c of listCells()) {
      S.stats.renders++;
      const r = readCell(c);
      if (!r) continue;
      ids.add(r.id);
      const prev = S.dom.get(r.id);
      if (!prev) { S.dom.set(r.id, { h: r.h, follow: r.follow, badge: r.badge, conflict: false }); continue; }
      if (!same(prev.h, r.h)) prev.conflict = true;   // one id shown with two usernames: trust neither
      prev.follow = r.follow;                         // the latest state of the button wins
      if (r.badge) prev.badge = true;
    }
    return ids;
  };
  let pending = false;
  const observer = new MutationObserver(() => {
    if (pending) return;
    pending = true;
    setTimeout(() => { pending = false; if (S.status === 'running') collect(); }, 50);
  });

  // ---- driving the scroll ----------------------------------------------------------------------------------------
  const atBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
  // X's own Retry for this list: inside the list itself, in a cell after the last account (where the list shows
  // its error), the only button there whose whole label says Retry, visible and enabled. Anywhere else, two such
  // buttons, or none, and nothing is pressed (a page-wide error page is left to a person).
  const retryButton = () => {
    const col = q('[data-testid="primaryColumn"]');
    if (!col) return null;
    const cells = qa('[data-testid="cellInnerDiv"]', col);
    const last = cells.map(c => !!q('[data-testid="UserCell"]', c)).lastIndexOf(true);
    if (last < 0) return null;
    const list = cells[last].parentNode;
    const tail = cells.slice(last + 1).filter(c => c.parentNode === list && !q('[data-testid="UserCell"]', c));
    const found = [].concat(...tail.map(c => qa('button, [role="button"]', c))).filter(b =>
      !b.closest('article, [role="dialog"], [role="menu"]') &&
      RETRY_TEXT.test((b.textContent || '').trim()) && !b.disabled && b.getAttribute('aria-disabled') !== 'true' &&
      !!(b.getClientRects && b.getClientRects().length));
    return found.length === 1 ? found[0] : null;
  };
  const loading = () => !!q('[data-testid="primaryColumn"] [role="progressbar"]');
  const nap = async ms => {
    const t0 = Date.now();
    await new Promise(r => setTimeout(r, ms));
    if (Date.now() - t0 > ms + LATE_MS) S.late = true;
  };
  const overlaps = (a, b) => { for (const x of a) if (b.has(x)) return true; return false; };
  // SHA-256 in plain JavaScript (FIPS 180-4): synchronous, so finishing has no await between the last check and
  // the save. Tested against Python's hashlib in tests/test_browser.py.
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  const sha256 = text => {
    const msg = new TextEncoder().encode(text);
    const n = ((msg.length + 9 + 63) >> 6) << 6;
    const buf = new Uint8Array(n);
    buf.set(msg);
    buf[msg.length] = 0x80;
    const dv = new DataView(buf.buffer);
    dv.setUint32(n - 8, Math.floor(msg.length / 0x20000000));
    dv.setUint32(n - 4, (msg.length * 8) >>> 0);
    const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const w = new Uint32Array(64);
    const rotr = (x, k) => (x >>> k) | (x << (32 - k));
    for (let off = 0; off < n; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) >>> 0;
        const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
        hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    return Array.from(h, x => x.toString(16).padStart(8, '0')).join('');
  };
  window.__xfaSha256 = sha256;   // for the tests
  let deadline = 0;
  const verdict = () => {   // anything that ends the read now, checked before every step and after every wait
    if (S.stop) return 'stopped';
    if (S.late) return 'paused';
    if (Date.now() > deadline) return 'timeout';
    if (whyNotOwner()) return 'account_changed';
    if (!same(pageHandle(), owner.handle)) return 'navigated';
    return null;
  };

  const finish = status => {   // synchronous from the last check to the save
    S.status = 'finishing';
    observer.disconnect();
    unhook();
    if (status !== 'account_changed') collect();
    const N = S.netStats;
    // The replies are the list; the page is the cross-check. A followed account the page showed but no reply
    // brought means a reply went unheard.
    S.stats.dom_only = Array.from(S.dom).filter(([id, d]) => d.follow === 'unfollow' && !S.net.has(id)).length;
    const entries = [], excluded = [];
    const handles = new Map();
    for (const x of S.net.values()) handles.set(x.h.toLowerCase(), (handles.get(x.h.toLowerCase()) || 0) + 1);
    for (const [id, x] of S.net) {
      const d = S.dom.get(id);
      if (x.conflict || (d && d.conflict) || (d && !same(d.h, x.h)) || handles.get(x.h.toLowerCase()) > 1) {
        excluded.push({ id, why: 'conflict' });
        continue;
      }
      const followDom = d ? d.follow === 'unfollow' : null;
      if (x.following !== null && followDom !== null && x.following !== followDom) {   // either way round
        excluded.push({ id, why: 'conflict' });
        continue;
      }
      const following = x.following !== null ? x.following : followDom;
      if (following === null) { excluded.push({ id, why: 'unknown_follow' }); continue; }
      if (!following) { excluded.push({ id, why: 'not_following' }); continue; }
      // follows you: X's explicit field; the page's mark fills a missing one; the mark against "false" is a
      // conflict the profile check must not resolve later
      let fy = x.followed_by, fc = false;
      if (d && d.badge) { if (fy === false) { fy = null; fc = true; } else fy = true; }
      entries.push({ id, h: x.h, fy, fc });
    }
    const unexplained = excluded.filter(e => e.why !== 'not_following').length;
    const endSignal = N.pages > 0 && N.end_seen;
    if (status === 'done') status = verdict() || 'done';   // the very last check; nothing runs between it and the save
    if (status === 'done') {
      if (!N.first_heard) status = 'missed_first_page';
      else if (N.unrecovered || N.chain_breaks || N.malformed || S.stats.dom_only || unexplained) status = 'stalled';
      else if (!endSignal) status = 'no_end_signal';
      else if (!entries.length) status = 'empty';
    }
    S.finished = new Date().toISOString();
    const doc = {
      kind: 'xfa-following', v: 1, tool: 'following.js ' + VERSION, capture_id: CONFIG.capture_id,
      owner: { id: owner.id, handle: owner.handle }, page: '/' + owner.handle + '/following',
      started: S.started, finished: S.finished, status, end_signal: status === 'done' && endSignal,
      network: Object.assign({}, N), dom: Object.assign({}, S.stats), entries, excluded,
    };
    // The digest covers the whole file except itself, as JSON.stringify writes it (all of it is ASCII).
    S.digest = sha256(JSON.stringify(doc));
    doc.digest = S.digest;
    if (status === 'account_changed') {   // never keep a list that may belong to another account
      S.text = null;
    } else {
      S.text = JSON.stringify(doc);
      S.file = 'xfa-following-' + owner.handle + '-' + CONFIG.capture_id + '.json';
      try {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([S.text], { type: 'application/json' }));
        a.download = S.file;
        document.body.appendChild(a);
        a.click();
        a.remove();
        S.downloaded = true;
      } catch (e) {
        S.downloaded = false;
        say('download failed: ' + e + '; use copy(xfaFollowingText()) instead');
      }
    }
    S.status = status;
    say(status + ': ' + entries.length + ' accounts' + (S.file ? '; saved as ' + S.file : ''));
  };

  const run = async () => {
    deadline = Math.min(CONFIG.deadline, Date.now() + MAX_MINUTES * 60000);
    // open the list inside the page, the way the side bar link does, so its first page is heard too
    history.pushState({}, '', '/' + owner.handle + '/following');
    dispatchEvent(new PopStateEvent('popstate', { state: {} }));
    for (let i = 0; i < 30 && !q('[data-testid="primaryColumn"] [data-testid="UserCell"]'); i++) await nap(500);
    const col = q('[data-testid="primaryColumn"]');
    if (col) observer.observe(col, { childList: true, subtree: true });
    await nap(1000);
    let quiet = 0;
    let prev = collect();
    for (;;) {
      const v = verdict();
      if (v) return finish(v);
      if (document.hidden) { S.stats.hidden_waits++; quiet = 0; await nap(1000); continue; }
      if (!atBottom()) {
        window.scrollBy(0, Math.max(200, Math.floor(window.innerHeight * STEP)));
        S.stats.steps++;
        quiet = 0;
        await nap(PAUSE_MS);
        const cur = collect();
        if (prev.size && cur.size && !overlaps(prev, cur)) S.stats.gaps++;   // for the record; the replies are the list
        if (cur.size) prev = cur;
        continue;
      }
      // a list request failed and waits for a retry: after a minute press X's own Retry (a few times at most, a
      // minute apart), and give the page up to RETRY_WAIT_MS before calling it the end
      if (S.pending && Date.now() - S.failedAt < RETRY_WAIT_MS) {
        const rb = Date.now() - S.failedAt >= RETRY_AFTER_MS && Date.now() - S.lastRetry >= RETRY_AFTER_MS &&
          S.stats.retries < MAX_RETRIES ? retryButton() : null;
        if (rb) {
          S.stats.retries++;
          S.lastRetry = Date.now();
          rb.click();
        }
        quiet = 0;
        await nap(5000);
        continue;
      }
      const before = S.dom.size + S.net.size;
      await nap(BOTTOM_WAIT_MS);
      if (verdict() || document.hidden) continue;   // ended, or hidden while waiting: that wait proves nothing
      const cur = collect();
      if (cur.size) prev = cur;
      if (S.dom.size + S.net.size > before || loading() || !atBottom()) { quiet = 0; continue; }
      // a failed page: give the page's own Retry (or a person pressing it) some time before calling it the end
      quiet++;
      if (quiet === Math.ceil(END_CHECKS / 2)) {   // nudge: some lists load more only after a fresh scroll
        window.scrollBy(0, -window.innerHeight);
        S.stats.nudges++;
        await nap(800);
        window.scrollTo(0, document.documentElement.scrollHeight);
      }
      if (quiet >= END_CHECKS) return finish(verdict() || 'done');
    }
  };

  const refuse = why => {
    S.status = 'refused: ' + why;
    window.__xfaFollowingStart = S.status;
    say(S.status);
  };
  const first = owner.id && owner.handle ? whyNotOwner() : (owner.id ? 'no profile link in the side bar (make the window wider)'
    : 'not signed in to X here (no twid cookie)');
  if (first) return refuse(first);
  if (pageHandle()) return refuse('reload x.com/home and run it there: on a Following page it cannot hear the first page');
  if (!navigator.locks || typeof navigator.locks.request !== 'function') return refuse('this browser has no Web Locks');
  navigator.locks.request('xfa-runner-' + owner.id, { ifAvailable: true }, async lock => {
    if (!lock) return refuse('an unfollow batch is running in this browser; wait for it to end');
    try {
      hook();
      window.__xfaFollowingStart = 'started: reading the Following list of @' + owner.handle + ' (read-only, up to ' +
        Math.round((Math.min(CONFIG.deadline, Date.now() + MAX_MINUTES * 60000) - Date.now()) / 60000) + ' minutes)';
      say(window.__xfaFollowingStart);
      await run();
    } catch (e) {
      S.status = 'error: ' + e;
      say(S.status);
    } finally {
      observer.disconnect();
      unhook();
    }
  });
})();
