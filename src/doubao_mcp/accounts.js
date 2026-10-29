// Account metadata and clicks stay in the client's own account menu.
// Never read cookies, storage, session keys, or call passport APIs directly.
const doubaoAccounts = (() => {
  const AVATAR = '[data-testid="chat_header_avatar_button"]';
  const MENU = '[data-testid="chat_header_menu"]';
  const ROW = '[data-testid="avatar_menu_account_row"]';
  const ADD = '[data-testid="avatar_menu_add_account"]';
  const visible = e => !!e && !!e.getClientRects().length;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const fail = (code, message, details = {}) => { throw Object.assign(new Error(message), {code, details}); };
  const validId = id => typeof id === 'string' && /^\d{1,30}$/.test(id);
  function fiber(e) {
    if (!e) return null;
    const raw = e[Object.keys(e).find(key => key.startsWith('__reactFiber$'))];
    // Host nodes can retain the alternate from a previous React commit.
    for (const candidate of [raw, raw?.alternate]) {
      if (!candidate) continue;
      let root = candidate;
      for (let i = 0; root.return && i < 200; i++) root = root.return;
      if (root.stateNode?.current === root) return candidate;
    }
    fail('ACCOUNT_IDENTITY_UNAVAILABLE', '账号组件正在更新，未读取可能过期的账号身份。');
  }
  function hooks(f) {
    const values = [];
    for (let h = f?.memoizedState, i = 0; h && i < 100; h = h.next, i++) values.push(h.memoizedState);
    return values;
  }
  function publicProfile(p, currentId) {
    return {account_id:p.entity_id, nickname:p.nickname, current:p.entity_id === currentId};
  }
  function current() {
    const ids = new Map();
    for (let f = fiber(document.querySelector(AVATAR)), i = 0; f && i < 40; f = f.return, i++) {
      if (typeof f.type !== 'function') continue;
      for (const value of hooks(f)) {
        // Only these two public fields cross the renderer boundary.
        if (value && validId(value.user_id_str) && typeof value.name === 'string')
          ids.set(value.user_id_str, {account_id:value.user_id_str, nickname:value.name});
      }
    }
    if (ids.size !== 1) fail('ACCOUNT_IDENTITY_UNAVAILABLE', '无法确认唯一的当前账号；请确认已登录，或检查客户端版本。');
    return {ok:true, background:true, current_account:[...ids.values()][0]};
  }
  async function waitFor(getter, message, attempts = 30) {
    for (let i = 0; i < attempts; i++) {
      const result = getter();
      if (result) return result;
      await sleep(100);
    }
    fail('ACCOUNT_MENU_UNAVAILABLE', message);
  }
  async function openMenu() {
    const button = document.querySelector(AVATAR)?.closest('button');
    if (!button || button.disabled) fail('ACCOUNT_MENU_UNAVAILABLE', '未找到可用的账号菜单。');
    const owned = button.getAttribute('aria-expanded') !== 'true';
    if (owned) {
      button.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, cancelable:true,
        button:0, buttons:1, pointerType:'mouse', isPrimary:true}));
      button.dispatchEvent(new PointerEvent('pointerup', {bubbles:true, cancelable:true,
        button:0, buttons:0, pointerType:'mouse', isPrimary:true}));
    }
    await waitFor(() => visible(document.querySelector(MENU)), '账号菜单未展开。');
    return owned;
  }
  async function closeMenu() {
    // Submenus consume the first Escape; the parent consumes the next.
    for (let i = 0; i < 4 && visible(document.querySelector(MENU)); i++) {
      (document.activeElement || document.querySelector(MENU)).dispatchEvent(new KeyboardEvent('keydown',
        {key:'Escape', code:'Escape', bubbles:true, cancelable:true}));
      await sleep(100);
    }
  }
  function menuProfiles() {
    const anchor = document.querySelector(ADD) || document.querySelector(ROW);
    for (let f = fiber(anchor), i = 0; f && i < 40; f = f.return, i++) {
      // This mounted component owns the exact profiles shown in the menu.
      if (typeof f.type !== 'function' || !/multiUserProfile/.test(f.type.toString()) ||
          !/handleSwitchAccount/.test(f.type.toString())) continue;
      const candidates = [];
      for (const value of hooks(f)) {
        const items = Array.isArray(value) ? value : value?.current;
        if (Array.isArray(items) && items.length && items.length <= 100 && items.every(p =>
          p && validId(p.entity_id) && typeof p.nickname === 'string')) candidates.push(items);
      }
      if (!candidates.length) return null;
      const keys = candidates.map(items => JSON.stringify(items.map(p => [p.entity_id,p.nickname]).sort()));
      if (!keys.every(key => key === keys[0])) return null; // Refresh still in progress.
      const {current_account} = current();
      const profiles = [...new Map(candidates[0].map(p => [p.entity_id,publicProfile(p,current_account.account_id)])).values()];
      if (!profiles.some(p => p.current)) return null;
      return {ok:true, background:true, current_account, accounts:profiles,
        count:profiles.length, can_switch:profiles.length > 1, login_required_for_new_account:true};
    }
    return null;
  }
  async function list() {
    const owned = document.querySelector(AVATAR)?.closest('button')?.getAttribute('aria-expanded') !== 'true';
    try {
      await openMenu();
      return await waitFor(menuProfiles, '未能读取已添加账号，请检查客户端版本或登录状态。');
    } finally {
      if (owned) await closeMenu();
    }
  }
  function rowIdentity(row) {
    for (let f = fiber(row), i = 0; f && i < 24; f = f.return, i++) {
      const p = f.memoizedProps;
      if (p?.profile && validId(p.profile.entity_id) && typeof p.handleSwitchAccount === 'function' && validId(p.curUid))
        return {account_id:p.profile.entity_id, current_id:p.curUid, loading:!!p.loading};
    }
    return null;
  }
  function matchingRows(id) {
    return [...document.querySelectorAll(ROW)].filter(visible).filter(e => rowIdentity(e)?.account_id === id);
  }
  async function targetRow(id) {
    let rows = matchingRows(id);
    if (!rows.length) {
      const more = [...document.querySelectorAll(`${MENU} [data-slot="dropdown-menu-sub-trigger"]`)]
        .filter(visible).filter(e => /更多.*[账帐]号|more\s+accounts/i.test(e.textContent));
      if (more.length === 1) {
        more[0].click();
        await waitFor(() => matchingRows(id).length, '更多账号菜单中未找到目标账号。');
        rows = matchingRows(id);
      }
    }
    if (rows.length !== 1) fail('ACCOUNT_ROW_UNAVAILABLE', '无法确定唯一的目标账号菜单项。');
    return rows[0];
  }
  async function preflight({account_id}) {
    doubao.check_idle();
    if ([...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].some(visible))
      fail('ACCOUNT_DIALOG_PRESENT', '豆包有未处理的弹窗，请先完成或关闭后再切换账号。');
    const data = await list();
    if (!data.accounts.some(p => p.account_id === account_id)) fail('ACCOUNT_NOT_FOUND', '目标账号未添加到客户端。');
    return {ok:true, proof:{from_account_id:data.current_account.account_id, account_id,
      route:location.pathname}, already_active:data.current_account.account_id === account_id};
  }
  async function switchOnce({account_id, proof}) {
    if (!proof || proof.account_id !== account_id || !validId(proof.from_account_id))
      fail('ACCOUNT_SWITCH_UNVERIFIED', '缺少账号切换前的核验信息。');
    let owned = false;
    try {
      doubao.check_idle();
      if (location.pathname !== proof.route || current().current_account.account_id !== proof.from_account_id)
        fail('ACCOUNT_CONTEXT_CHANGED', '账号或页面发生变化，未切换。');
      if (proof.from_account_id === account_id) return {ok:true, clicked:false, already_active:true};
      owned = document.querySelector(AVATAR)?.closest('button')?.getAttribute('aria-expanded') !== 'true';
      await openMenu();
      const row = await targetRow(account_id);
      const identity = rowIdentity(row);
      doubao.check_idle();
      if (location.pathname !== proof.route || current().current_account.account_id !== proof.from_account_id ||
          identity?.current_id !== proof.from_account_id)
        fail('ACCOUNT_CONTEXT_CHANGED', '切换前账号或页面发生变化，未点击。');
      if (identity.loading || row.getAttribute('aria-disabled') === 'true' || row.hasAttribute('data-disabled'))
        fail('ACCOUNT_SWITCH_BUSY', '豆包正在处理账号操作，未重复点击。');
      owned = false; // Radix closes on selection; a reload must not run another UI action.
      row.click(); // The sole switch attempt. No direct account API, no retries.
      return {ok:true, clicked:true, target_account_id:account_id};
    } finally {
      if (owned) await closeMenu();
    }
  }
  function observe() {
    const dialogs = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')].filter(visible);
    const loginRequired = dialogs.some(e => /登录|扫码|验证码|login|sign\s*in/i.test(e.textContent));
    let data = null;
    try { data = current().current_account; } catch {}
    return {ok:true, background:true, current_account:data, login_required:loginRequired,
      dialog_present:dialogs.length > 0, editor_available:visible(document.querySelector('[data-testid="chat_input_input"]'))};
  }
  return {current, list, preflight, switchOnce, observe};
})();
