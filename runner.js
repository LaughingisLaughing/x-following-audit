// x-following-audit browser runner: one bounded, paced batch of unfollows on x.com.
// Rendered by `xfa.py batch`, which replaces the config placeholder below. The start message
// (started / refused and why) is also kept in window.__xfaStartMessage, because some tools
// cannot show the value of an async expression; results: xfaResult().
(() => { const xfaStart = (async () => {
  'use strict';
  const c = /*XFA_CONFIG*/null;
  const obj = x => x && typeof x === 'object' && !Array.isArray(x);
  const account = x => obj(x) && typeof x.id === 'string' && /^\d+$/.test(x.id) &&
    typeof x.handle === 'string' && /^[A-Za-z0-9_]{1,15}$/.test(x.handle);
  const tag = x => typeof x === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(x);
  if (!obj(c) || c.version !== 1 || !tag(c.batch_id) || !tag(c.plan_id) || !account(c.owner) ||
      typeof c.nonce !== 'string' || !/^[a-f0-9]{8,32}$/.test(c.nonce) ||
      !Array.isArray(c.subjects) || c.subjects.length > 15 ||
      !c.subjects.every(s => account(s) && (!Object.prototype.hasOwnProperty.call(s, 'mutual_ok') || typeof s.mutual_ok === 'boolean')) ||
      new Set(c.subjects.map(x => x.id)).size !== c.subjects.length ||
      new Set(c.subjects.map(x => x.handle.toLowerCase())).size !== c.subjects.length ||
      c.subjects.some(x => x.id === c.owner.id || x.handle.toLowerCase() === c.owner.handle.toLowerCase()) ||
      !Number.isSafeInteger(c.allowed) || c.allowed < 0 ||
      !Number.isFinite(c.min_gap_s) || c.min_gap_s < 0 || !Number.isFinite(c.max_gap_s) ||
      c.max_gap_s < c.min_gap_s || !Number.isFinite(c.max_gap_s * 1000) ||
      typeof c.expires_at !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(c.expires_at) ||
      !Number.isFinite(Date.parse(c.expires_at)) || typeof c.dry_run !== 'boolean' ||
      !['zh', 'en'].includes(c.lang)) return 'error: invalid XFA configuration';
  if (location.protocol !== 'https:' || location.hostname !== 'x.com') return 'refused: open an HTTPS x.com page';

  const HB = 'xfa_runner_hb_' + c.owner.id;
  const BATCH_PREFIX = 'xfa_batch_' + c.owner.id + '_';
  const KEY = BATCH_PREFIX + c.batch_id + '_' + c.nonce, LAST = 'xfa_latest_batch_' + c.owner.id;
  const FRESH = 90000, expires = Date.parse(c.expires_at);
  const now = () => new Date().toISOString();
  const read = key => JSON.parse(localStorage.getItem(key) || 'null');
  const fresh = hb => obj(hb) && Number.isFinite(hb.at) && Date.now() - hb.at < FRESH;
  const terminal = x => x && (x.status === 'done' || /^stopped: /.test(x.status));
  const installResult = () => {
    window.xfaResult = () => {
      const batch = window.__xfaBatch && window.__xfaBatch.owner_id === c.owner.id ? window.__xfaBatch : null;
      try {
        const key = localStorage.getItem(LAST);
        return JSON.stringify(batch || (key && key.startsWith(BATCH_PREFIX) && read(key)) || null);
      } catch (_) { return JSON.stringify(batch || null); }
    };
  };
  window.xfaStop = () => {
    const batch = window.__xfaBatch;
    if (!batch || !batch.running) return 'No batch is running';
    batch.stop = true;
    return 'Stop requested for ' + batch.batch_id;
  };
  if (window.__xfaRunner || (window.__xfaBatch && window.__xfaBatch.running))
    return 'refused: a batch is already running in this tab';
  let releaseLock, lockTask, lockHeld = false;
  const lockLife = new Promise(resolve => { releaseLock = resolve; });
  const hasLocks = !!navigator.locks && typeof navigator.locks.request === 'function';
  const acquireLock = () => new Promise(resolve => {
    try {
      lockTask = navigator.locks.request('xfa-runner-' + c.owner.id, {ifAvailable: true}, async lock => {
        if (!lock) { resolve(false); return; }
        lockHeld = true; resolve(true); await lockLife;
      });
      lockTask.catch(() => resolve(false));
    } catch (_) { resolve(false); }
  });
  let saved, previousHb;
  try { saved = read(KEY); }
  catch (_) { return 'error: localStorage unavailable or invalid'; }
  installResult();
  if (saved) {
    if (!terminal(saved)) {
      if (hasLocks) {
        if (!await acquireLock()) return 'refused: runner lock unavailable';
      } else if (Date.now() <= expires + 60000) {
        return 'refused: batch recovery requires expiry plus 60 seconds without Web Locks';
      }
    }
    try {
      // Re-read under the same account lock used by new runners.
      saved = read(KEY);
      if (!obj(saved) || saved.batch_id !== c.batch_id || saved.owner_id !== c.owner.id ||
          saved.plan_id !== c.plan_id || saved.nonce !== c.nonce || !Array.isArray(saved.results)) return 'refused: batch record mismatch';
      if (!terminal(saved)) {
        if (saved.status !== 'running') return 'refused: invalid batch record';
        if (Number.isFinite(saved.heartbeat_at) && Date.now() - saved.heartbeat_at < FRESH)
          return 'refused: this batch still has a fresh heartbeat';
        saved.status = 'stopped: page reloaded';
        saved.message = c.lang === 'zh' ? '已停止：页面已刷新' : 'Stopped: page reloaded';
        saved.finished_at = now(); saved.running = false; saved.stop = false;
        for (const s of c.subjects) {
          if (saved.results.some(r => r.id === s.id)) continue;
          const pending = saved.in_flight && saved.in_flight.id === s.id && saved.in_flight.clicked;
          saved.results.push({...s, clicked: !!pending, result: pending ? 'unknown' : 'not_started',
            path: pending ? saved.in_flight.path : 'none', note: pending ? 'Verification interrupted by reload' : 'Page reloaded', at: now()});
        }
        saved.in_flight = null;
      }
      try { localStorage.setItem(KEY, JSON.stringify(saved)); localStorage.setItem(LAST, KEY); }
      catch (_) { return 'error: cannot restore batch record'; }
      window.__xfaBatch = saved; installResult();
      return JSON.stringify(saved);
    } catch (_) { return 'error: cannot restore batch record'; }
    finally { releaseLock(); }
  }
  try { previousHb = read(HB); } catch (_) { return 'error: invalid runner heartbeat'; }
  if (fresh(previousHb)) return 'refused: another runner has a fresh heartbeat';
  const rid = c.batch_id + ':' + Date.now() + ':' + Math.random().toString(36).slice(2);
  window.__xfaRunner = rid;
  if (hasLocks && !await acquireLock()) { delete window.__xfaRunner; return 'refused: runner lock unavailable'; }

  const st = {batch_id: c.batch_id, plan_id: c.plan_id, owner_id: c.owner.id, nonce: c.nonce, status: 'running',
    message: c.lang === 'zh' ? '取关进行中' : 'Unfollowing in progress',
    started_at: now(), finished_at: null, dry_run: c.dry_run,
    identity: {signals: 0, twid_readable: false}, results: [], running: true, stop: false,
    heartbeat_at: Date.now(), attempts: 0, in_flight: null};
  let W = null, seq = 0, pulse = 0, stopped = '', lastClick = null, nextClick = 0;
  let active = null, titleObserver = null, toastObserver = null, pageGone = false;
  const callbacks = new Map();
  const prefix = c.lang === 'zh' ? '【取关进行中，请勿关闭】' : '[Unfollowing - keep this tab open] ';
  const q = (s, root = document) => root.querySelector(s);
  const qa = (s, root = document) => [...root.querySelectorAll(s)];
  const rnd = (a, b) => a + Math.random() * (b - a);
  const persist = () => {
    st.heartbeat_at = Date.now();
    localStorage.setItem(KEY, JSON.stringify(st)); localStorage.setItem(LAST, KEY);
  };
  const beat = () => {
    const hb = read(HB);
    if (fresh(hb) && hb.id !== rid) throw new Error('lock_lost');
    localStorage.setItem(HB, JSON.stringify({id: rid, batch_id: c.batch_id, at: Date.now(), status: st.status}));
    pulse = Date.now(); persist();
  };
  const rawSleep = ms => new Promise(resolve => {
    const id = ++seq, began = Date.now();
    let timer;
    const finish = () => {
      clearTimeout(timer); callbacks.delete(id);
      // A wait that ends a minute or more late means the computer slept (Grok Bot's cloud computer sleeps between
      // tasks; a Mac sleeps with its lid closed). Never carry on clicking after that: stop the batch.
      if (Date.now() - began > ms + 60000)
        stopped = stopped || (active && active.clicked ? 'verification_interrupted' : 'paused_too_long');
      resolve();
    };
    callbacks.set(id, finish);
    timer = setTimeout(finish, ms);
    if (W) { try { W.postMessage({id, ms}); } catch (_) { /* Main timer remains active. */ } }
  });
  const stop = reason => { if (!stopped) stopped = reason; throw new Error(stopped); };
  const inspectToasts = () => {
    if (qa('[data-testid="toast"]').some(e => /limit|later|限制|稍后|稍後|制限/i.test(e.textContent || '')))
      stopped = stopped || 'rate_limited';
  };
  const guard = () => {
    if (stopped) stop(stopped);
    if (st.stop) stop('user_requested');
    if (pageGone) stop('page_left');
    if (/^\/(?:login(?:\/|$)|i\/flow(?:\/|$)|account\/access(?:\/|$))/.test(location.pathname)) stop('login_or_verification');
    if (Date.now() >= expires) stop('expired');
    if (active && active.clicked && !inPath(active)) stop('page_left');
    try { const hb = read(HB); if (!hb || hb.id !== rid) stop('lock_lost'); }
    catch (_) { stop(stopped || 'heartbeat_or_storage_lost'); }
    inspectToasts(); if (stopped) stop(stopped);
    if (Date.now() - pulse >= 20000) {
      try { beat(); } catch (_) { stop('heartbeat_or_storage_lost'); }
    }
  };
  const wait = async ms => {
    const until = Date.now() + ms;
    do { guard(); await rawSleep(Math.max(0, Math.min(500, until - Date.now(), expires - Date.now()))); }
    while (Date.now() < until);
    guard();
  };
  const identity = () => {
    guard();
    const h = c.owner.handle.toLowerCase(), nav = q('[data-testid="AppTabBar_Profile_Link"]');
    const sw = q('[data-testid="SideNav_AccountSwitcher_Button"]');
    const a = !!nav && (nav.getAttribute('href') || '').toLowerCase() === '/' + h;
    const b = !!sw && ((sw.textContent || '').match(/@[A-Za-z0-9_]+/g) || []).some(x => x.toLowerCase() === '@' + h);
    let readable = false, cookieOK = false;
    try {
      const m = document.cookie.match(/(?:^|;\s*)twid=([^;]*)/);
      if (m) { readable = true; cookieOK = decodeURIComponent(m[1]).replace(/^"|"$/g, '') === 'u=' + c.owner.id; }
    } catch (_) { cookieOK = false; }
    st.identity = {signals: Number(a) + Number(b) + Number(readable && cookieOK), twid_readable: readable};
    if (!a || !b || (readable && !cookieOK)) stop('identity_mismatch');
  };
  const inPath = s => location.pathname.toLowerCase().replace(/\/$/, '') === '/' + s.handle.toLowerCase() ||
    location.pathname.replace(/\/$/, '') === '/i/user/' + s.id;
  // Ignore tweet authors, recommendations, dialogs and menus when locating profile controls.
  const header = () => {
    const col = q('[data-testid="primaryColumn"]');
    if (!col) return [];
    const firstPost = q('article[data-testid="tweet"]', col);
    return qa('[data-testid]', col).filter(e => !e.closest('article, [data-testid="UserCell"], [role="dialog"], [role="menu"]') &&
      (!firstPost || !!(e.compareDocumentPosition(firstPost) & 4)));
  };
  const subs = /subscrib|subscription|订阅|訂閱|サブスク|suscrib|abonnieren|s’abonner|s'abonner|assinar|подписаться/i;
  const follows = /^(?:Following|正在关注|正在關注|關注中|关注中|フォロー中|Siguiendo|Abonniert|Abonné(?:e)?|Seguindo|Подписки)(?=$|[\s,.:@…])/i;
  const controls = s => {
    const elements = header(), own = elements.filter(e => (e.getAttribute('data-testid') || '').startsWith(s.id + '-'));
    const un = own.filter(e => e.getAttribute('data-testid') === s.id + '-unfollow');
    const labelled = un.filter(e => follows.test(e.getAttribute('aria-label') || '') && !subs.test(e.getAttribute('aria-label') || ''));
    const following = labelled.length === 1 ? labelled[0] : un.length === 1 && !subs.test(un[0].getAttribute('aria-label') || '') ? un[0] : null;
    return {own, following, follow: own.find(e => e.getAttribute('data-testid') === s.id + '-follow'),
      subscription: own.some(e => subs.test((e.getAttribute('aria-label') || '') + ' ' + e.getAttribute('data-testid'))),
      foreign: elements.some(e => /^\d+-(?:unfollow|follow)$/.test(e.getAttribute('data-testid') || '') && !own.includes(e))};
  };
  const target = s => inPath(s) && controls(s).own.length > 0;
  const mutual = () => !!q('[data-testid="userFollowIndicator"]');
  const go = s => {
    guard(); history.pushState({}, '', '/i/user/' + s.id);
    dispatchEvent(new PopStateEvent('popstate', {state: {}}));
  };
  const waitControls = async s => {
    for (let i = 0; i < 40; i++) {
      await wait(500);
      const b = controls(s);
      if (inPath(s) && (b.following || b.follow || b.subscription)) return b;
    }
    return null;
  };
  const load = async s => {
    go(s); let b = await waitControls(s);
    if (b) return b;
    await wait(rnd(5000, 10000));
    const col = q('[data-testid="primaryColumn"]');
    const retry = col && qa('button, [role="button"]', col).find(e => /^(?:Retry|重试|重試|再試行|Reintentar|Réessayer|Erneut versuchen|Tentar novamente|Повторить)$/i.test((e.textContent || '').trim()));
    if (retry) { guard(); retry.click(); b = await waitControls(s); }
    if (!b) { go(s); b = await waitControls(s); }
    return b;
  };
  const dismissMenu = () => {
    const menu = q('[role="menu"]');
    let node = menu, backdrop = null;
    while (node && node.id !== 'layers' && !backdrop) {
      const p = node.parentElement;
      backdrop = p && [...p.children].find(e => {
        const r = e.getBoundingClientRect();
        return e !== node && r.width >= innerWidth * 0.9 && r.height >= innerHeight * 0.9;
      });
      node = p;
    }
    if (backdrop) backdrop.click();
    else if (menu) document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true}));
  };
  const closeMenu = async () => {
    for (let i = 0; i < 3 && q('[role="menu"]'); i++) {
      dismissMenu(); await wait(300);
    }
    return !q('[role="menu"]');
  };
  const outcome = (result, path, note) => ({result, path, note});
  const mismatch = (path, note, container) => {
    // Do not wait through guard(): this failure must be recorded before stopping.
    stopped = 'confirm_mismatch';
    try {
      const cancel = (container && q('[data-testid="confirmationSheetCancel"]', container)) || q('[data-testid="confirmationSheetCancel"]');
      if (cancel) cancel.click();
      if (q('[role="menu"]')) dismissMenu();
    } catch (_) { /* Keep the binding failure even if the overlay disappeared. */ }
    return outcome('failed', path, note);
  };
  const confirmationContainer = button => {
    const dialog = button.closest('[role="dialog"]');
    if (dialog) return dialog;
    // Without a dialog role, use the nearest shared sheet parent of confirm/cancel.
    for (let node = button.parentElement; node && node !== document.body && node.id !== 'layers'; node = node.parentElement)
      if (q('[data-testid="confirmationSheetCancel"]', node)) return node;
    return null;
  };
  // The exact handle (so @foo never matches @foobar), and an unfollow button, not block or mute.
  const containsHandle = (container, s) => !!container &&
    new RegExp('@' + s.handle + '(?![A-Za-z0-9_])', 'i').test(container.textContent || '');
  const unfollowAction = /^(?:Unfollow|取消关注|取消關注|フォロー解除|Dejar de seguir|Ne plus suivre|Se désabonner|Entfolgen|Deixar de seguir|Smetti di seguire|Ontvolgen|Отписаться|Перестать читать|팔로우 취소|Takibi bırak|Berhenti mengikuti|Hủy theo dõi)/i;
  const isUnfollowButton = button => unfollowAction.test((button.textContent || '').replace(/\s+/g, ' ').trim());
  const paceClick = () => {
    lastClick = Date.now(); nextClick = lastClick + rnd(c.min_gap_s, c.max_gap_s) * 1000;
  };
  const ready = async s => {
    // Only a new attempt needs a free slot; the confirmation of an attempt already counted goes on.
    if (!c.dry_run && !active.clicked && st.attempts >= c.allowed) stop('allowed_reached');
    if (!c.dry_run && !active.clicked && lastClick !== null && Date.now() < nextClick) await wait(nextClick - Date.now());
    identity();
    if (!target(s)) return outcome('failed', active.path, 'Target changed before click');
    if (mutual() && s.mutual_ok !== true) return outcome('mutual_now', active.path, 'Follow indicator present');
    return null;
  };
  // countAttempt is false for the confirmation after a Following click: that click was the attempt.
  const commit = (s, button, binding, countAttempt = true) => {
    identity();
    if (!target(s) || !button || !button.isConnected) return false;
    if (mutual() && s.mutual_ok !== true) return false;
    if (countAttempt && st.attempts >= c.allowed) stop('allowed_reached');
    guard();
    if (!binding()) return false;
    // Persist uncertainty before the relationship-changing click, including a possible reload.
    active.clicked = true; if (countAttempt) st.attempts++; persist();
    paceClick();
    button.click(); return true;
  };
  const byButton = async s => {
    active.path = 'button';
    let r = await ready(s); if (r) return r;
    const un = controls(s).following;
    if (!un || !un.isConnected) return outcome('failed', 'button', 'Following button missing');
    if (q('[data-testid="confirmationSheetConfirm"]') || q('[role="menu"]') || q('[role="dialog"]'))
      return outcome('failed', 'button', 'Existing overlay blocks confirmation');
    identity(); if (!target(s)) return outcome('failed', 'button', 'Target changed before opening confirmation');
    if (mutual() && s.mutual_ok !== true) return outcome('mutual_now', 'button', 'Follow indicator present');
    // A dry run never touches the Following button: should X ever unfollow without a
    // confirmation sheet, opening it would already unfollow.
    if (c.dry_run) return outcome('would_unfollow', 'button', 'Dry run: Following button and identity checked');
    r = await ready(s); if (r) return r;
    identity();
    if (!target(s) || !un.isConnected || controls(s).following !== un) return outcome('failed', 'button', 'Following button changed');
    if (mutual() && s.mutual_ok !== true) return outcome('mutual_now', 'button', 'Follow indicator present');
    // The Following click is the attempt: it may already unfollow, so it counts toward allowed.
    if (st.attempts >= c.allowed) stop('allowed_reached');
    active.confirm_opened = true; active.clicked = true; st.attempts++; persist();
    paceClick(); un.click();
    let confirm = null;
    for (let i = 0; i < 10 && !confirm; i++) { await wait(300); confirm = q('[data-testid="confirmationSheetConfirm"]'); }
    if (!confirm) {
      identity();
      const b = controls(s);
      if (target(s) && b.follow && !b.following) {
        return outcome('unfollowed', 'button', 'Unfollowed without a confirmation sheet');
      }
      return outcome('failed', 'button', 'Confirmation sheet missing');
    }
    const container = confirmationContainer(confirm);
    const binding = () => confirm.isConnected && q('[data-testid="confirmationSheetConfirm"]') === confirm &&
      !!container && container.isConnected && container.contains(confirm) && containsHandle(container, s) &&
      isUnfollowButton(confirm);
    if (!binding()) return mismatch('button', 'Confirmation sheet target mismatch', container);
    await wait(rnd(600, 1500));
    if (!binding()) return mismatch('button', 'Confirmation sheet changed before click', container);
    r = await ready(s); if (r) return r;
    if (!binding()) return mismatch('button', 'Confirmation sheet changed before click', container);
    if (!controls(s).following) return outcome('failed', 'button', 'Target button changed');
    if (!commit(s, confirm, binding, false)) {
      if (!binding()) return mismatch('button', 'Confirmation sheet changed before click', container);
      return outcome(mutual() && s.mutual_ok !== true ? 'mutual_now' : 'failed', 'button', 'Final click cancelled');
    }
    const deadline = Date.now() + 7000;
    while (Date.now() < deadline) {
      await wait(500);
      if (!target(s) || !navigator.onLine || pageGone) return outcome('unknown', 'button', 'Verification interrupted');
      identity();
      const b = controls(s);
      if (b.follow && !b.following) return outcome('unfollowed', 'button', 'Follow button confirmed');
    }
    return outcome(Date.now() > deadline + 60000 ? 'unknown' : 'failed', 'button', 'Follow button not confirmed');
  };
  const menuItem = (items, s, unfollow) => {
    const h = '@' + s.handle;
    const patterns = unfollow ? [
      '(?:Unfollow|取消关注|取消關注|フォロー解除|Deixar de seguir|Перестать читать|Отписаться от)\\s+' + h,
      'Dejar de seguir(?: a)?\\s+' + h, '(?:Se désabonner de|Ne plus suivre)\\s+' + h,
      h + '\\s+entfolgen', h + '(?:さん)?のフォローを解除'
    ] : [
      '(?:Follow|关注|關注|フォロー|Seguir|Suivre|Читать)\\s+' + h,
      h + '\\s+folgen', h + '(?:さん)?をフォロー'
    ];
    const re = new RegExp('^(?:' + patterns.join('|') + ')$', 'i');
    return items.find(e => re.test((e.textContent || '').replace(/\s+/g, ' ').trim()));
  };
  const authorMatches = (article, s) => {
    const author = article && q('[data-testid="User-Name"]', article);
    return !!author && qa('a[href]', author).some(link =>
      (link.getAttribute('href') || '').toLowerCase() === '/' + s.handle.toLowerCase());
  };
  const openMenu = async s => {
    for (let i = 0; i < 16; i++) {
      guard(); if (!target(s)) return null;
      const article = qa('article[data-testid="tweet"]').find(a => authorMatches(a, s));
      const caret = article && q('[data-testid="caret"]', article);
      if (caret) {
        if (q('[role="menu"]') || q('[role="dialog"]') || q('[data-testid="confirmationSheetConfirm"]')) return null;
        identity();
        if (!target(s) || !caret.isConnected || !authorMatches(article, s)) return null;
        const existingMenus = new Set(qa('[role="menu"]'));
        caret.click();
        for (let k = 0; k < 10; k++) {
          await wait(300);
          if (!target(s)) return null;
          const menus = qa('[role="menu"]');
          if (menus.length === 1 && !existingMenus.has(menus[0])) {
            const menu = menus[0], items = qa('[role="menuitem"]', menu);
            if (items.length) return {menu, items, article, caret};
          }
        }
        return null;
      }
      await wait(500);
    }
    return null;
  };
  const byMenu = async s => {
    active.path = 'menu';
    let r = await ready(s); if (r) return r;
    if (!await closeMenu()) return outcome('failed', 'menu', 'Previous menu still open');
    let opened = await openMenu(s);
    if (!opened) return outcome('failed', 'menu', 'Author post menu unavailable');
    const un = menuItem(opened.items, s, true);
    if (!un) {
      const follow = menuItem(opened.items, s, false);
      await closeMenu();
      return outcome(follow ? 'already_not_following' : 'failed', 'menu', follow ? 'Follow menu item already present' : 'Exact unfollow menu item missing');
    }
    const binding = () => opened.menu.isConnected && opened.article.isConnected && opened.caret.isConnected &&
      authorMatches(opened.article, s) && qa('[role="menu"]').length === 1 && q('[role="menu"]') === opened.menu &&
      un.isConnected && un.closest('[role="menu"]') === opened.menu &&
      menuItem(qa('[role="menuitem"]', opened.menu), s, true) === un;
    await wait(rnd(800, 1600));
    if (!binding()) return mismatch('menu', 'Author post menu changed before click');
    r = await ready(s); if (r) return r;
    if (!binding()) return mismatch('menu', 'Author post menu changed before click');
    if (c.dry_run) { await closeMenu(); return outcome('would_unfollow', 'menu', 'Dry run menu checked'); }
    if (!commit(s, un, binding)) {
      if (!binding()) return mismatch('menu', 'Author post menu changed before click');
      return outcome(mutual() && s.mutual_ok !== true ? 'mutual_now' : 'failed', 'menu', 'Menu click cancelled');
    }
    await wait(2500);
    if (!target(s) || !navigator.onLine) return outcome('unknown', 'menu', 'Verification interrupted');
    identity();
    if (!await closeMenu()) return outcome('failed', 'menu', 'Menu could not be closed');
    opened = await openMenu(s);
    if (!target(s) || !navigator.onLine) return outcome('unknown', 'menu', 'Verification interrupted');
    const ok = opened && menuItem(opened.items, s, false);
    await closeMenu();
    return outcome(ok ? 'unfollowed' : 'failed', 'menu', ok ? 'Follow menu item confirmed' : 'Follow menu item not confirmed');
  };
  const record = (s, r) => {
    try {
      if (active && active.confirm_opened) {
        const cancel = q('[data-testid="confirmationSheetCancel"]'); if (cancel) cancel.click();
      }
    } catch (_) { /* Overlay cleanup must not replace the observed result. */ }
    st.results.push({...s, ...r, clicked: !!(active && active.clicked), at: now()}); st.in_flight = null; active = null; persist();
  };
  const fixTitle = () => {
    if (st.running) { if (!document.title.startsWith(prefix)) document.title = prefix + document.title; }
    else if (document.title.startsWith(prefix)) document.title = document.title.slice(prefix.length);
  };
  const leave = () => { pageGone = true; };
  const run = async () => {
    let failures = 0;
    try {
      identity();
      for (let i = 0; i < c.subjects.length; i++) {
        const s = c.subjects[i]; identity();
        active = {id: s.id, handle: s.handle, path: 'none', clicked: false}; st.in_flight = active; persist();
        const b = await load(s);
        let r;
        if (!b) r = outcome(!inPath(s) || controls(s).foreign ? 'failed' : 'unavailable', 'none', 'Profile controls unavailable after retry');
        else if (!target(s)) r = outcome('failed', 'none', 'Profile target mismatch');
        else if (mutual() && s.mutual_ok !== true) r = outcome('mutual_now', 'none', 'Follow indicator present');
        else if (b.follow && !b.following) r = outcome('already_not_following', 'none', 'Follow button already present');
        else r = b.following ? await byButton(s) : await byMenu(s);
        record(s, r);
        failures = r.result === 'failed' ? failures + 1 : 0;
        if (failures >= 3) stop('three_failures');
        guard();
        if (i + 1 < c.subjects.length) await wait(rnd(8000, 15000));
      }
      st.status = 'done';
      st.message = c.lang === 'zh' ? '批次已完成' : 'Batch completed';
    } catch (_) {
      st.status = 'stopped: ' + (stopped || 'internal_error');
      const reasons = {user_requested: '用户停止', expired: '批次已到期', identity_mismatch: '身份不符',
        login_or_verification: '需要登录或验证', rate_limited: '出现限制提示', three_failures: '连续三次失败',
        allowed_reached: '已达本批点击上限', page_left: '已离开目标页面', heartbeat_or_storage_lost: '心跳或存储不可用',
        lock_lost: '执行锁已失效', verification_interrupted: '结果核对被打断', confirm_mismatch: '确认目标不符',
        paused_too_long: '电脑休眠过，本批停止'};
      st.message = c.lang === 'zh' ? '已停止：' + (reasons[stopped] || '运行异常') : 'Stopped: ' + (stopped || 'internal_error');
      if (active) {
        const s = c.subjects.find(s => s.id === active.id);
        st.results.push({...s, clicked: !!active.clicked, result: active.clicked ? 'unknown' : 'not_started', path: active.path,
          note: active.clicked ? 'Verification interrupted' : 'Stopped before relationship click', at: now()});
      }
      for (const s of c.subjects) if (!st.results.some(r => r.id === s.id))
        st.results.push({...s, result: 'not_started', clicked: false, path: 'none', note: 'Batch stopped', at: now()});
    } finally {
      try {
        if (active && active.confirm_opened) {
          const cancel = q('[data-testid="confirmationSheetCancel"]'); if (cancel) cancel.click();
        }
      } catch (_) { /* Always release timers and the lease. */ }
      st.running = false; st.in_flight = null; st.finished_at = now();
      try { persist(); } catch (_) { st.status = 'stopped: storage_error'; }
      if (titleObserver) titleObserver.disconnect();
      if (toastObserver) toastObserver.disconnect();
      fixTitle(); window.removeEventListener('pagehide', leave);
      if (W) W.terminate();
      for (const finish of [...callbacks.values()]) finish();
      try { const hb = read(HB); if (hb && hb.id === rid) localStorage.removeItem(HB); } catch (_) { /* Preserve other leases. */ }
      if (window.__xfaRunner === rid) delete window.__xfaRunner;
      releaseLock();
    }
  };
  try {
    if (read(KEY)) throw new Error('batch appeared during startup');
    if (fresh(read(HB))) throw new Error('runner already active');
    if (!lockHeld) {
      localStorage.setItem(HB, JSON.stringify({id: rid, at: Date.now(), status: 'running'}));
      await rawSleep(150);
      const hb = read(HB);
      if (!hb || hb.id !== rid) throw new Error('fallback lease unavailable');
    }
    beat(); window.__xfaBatch = st; installResult();
    try {
      const url = URL.createObjectURL(new Blob(['onmessage=e=>setTimeout(()=>postMessage(e.data.id),e.data.ms)'], {type: 'text/javascript'}));
      try { W = new Worker(url); W.onmessage = e => { const finish = callbacks.get(e.data); if (finish) finish(); }; }
      finally { URL.revokeObjectURL(url); }
    } catch (_) { W = null; }
    titleObserver = new MutationObserver(fixTitle);
    titleObserver.observe(document.head, {subtree: true, childList: true, characterData: true}); fixTitle();
    toastObserver = new MutationObserver(inspectToasts);
    toastObserver.observe(document.documentElement, {subtree: true, childList: true, characterData: true});
    window.addEventListener('pagehide', leave);
    void run();
    return st.running ? 'started ' + c.batch_id + ': ' + c.subjects.length + ' subjects, allowed ' + c.allowed + ', dry_run ' + c.dry_run : st.status + ' ' + c.batch_id;
  } catch (_) {
    st.running = false;
    if (titleObserver) titleObserver.disconnect();
    if (toastObserver) toastObserver.disconnect();
    fixTitle(); if (W) W.terminate();
    try { const hb = read(HB); if (hb && hb.id === rid) localStorage.removeItem(HB); } catch (_) { /* No lease to release. */ }
    if (window.__xfaRunner === rid) delete window.__xfaRunner;
    releaseLock(); return 'refused: runner startup or storage unavailable';
  }
})();
  xfaStart.then(m => { window.__xfaStartMessage = m; },
    e => { window.__xfaStartMessage = 'error: ' + ((e && e.message) || e); });
  return xfaStart;
})()
