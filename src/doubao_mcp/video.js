// Video controls use DOM events inside the renderer, never native input.
const doubaoVideo = (() => {
  const MODEL = '[data-input-engine-actionbar-control-key="video-model"]';
  const PARAMS = '[data-input-engine-actionbar-render-entry-key="video-generation-params-panel"]';
  const all = (s, root = document) => [...root.querySelectorAll(s)];
  const text = e => (e?.textContent ?? '').trim();
  const fail = (code, message) => { throw Object.assign(new Error(message), {code}); };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const toggle = button => {
    for (const type of ['pointerdown', 'pointerup']) button.dispatchEvent(new PointerEvent(type, {
      bubbles:true, cancelable:true, button:0, buttons:type === 'pointerdown' ? 1 : 0,
      pointerType:'mouse', isPrimary:true
    }));
  };
  const active = () => !!document.querySelector(MODEL);
  const disabled = e => e.disabled || (e.hasAttribute('data-disabled') && e.getAttribute('data-disabled') !== 'false') || e.getAttribute('aria-disabled') === 'true';
  async function until(test, label) {
    for (let i = 0; i < 45; i++) {
      const value = test();
      if (value) return value;
      await sleep(100);
    }
    fail('VIDEO_CONTROL_TIMEOUT', '等待视频控件超时：' + label);
  }
  function guard(route) {
    if (location.pathname !== route) fail('CONVERSATION_CHANGED', '设置期间豆包页面被切换，已停止。');
    doubao.check_idle();
  }
  function owned(menu) {
    return [MODEL, PARAMS].some(s => document.querySelector(s)?.id === menu.getAttribute('aria-labelledby'));
  }
  async function closeMenu() {
    for (const menu of all('[role="menu"][data-state="open"]')) {
      if (!owned(menu)) fail('OTHER_MENU_OPEN', '豆包有其他菜单打开，请先关闭后再设置视频。');
      for (let i = 0; menu.isConnected && menu.dataset.state === 'open'; i++) {
        if (i >= 20) fail('VIDEO_CONTROL_TIMEOUT', '等待视频控件超时：关闭菜单');
        menu.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', code:'Escape', bubbles:true, cancelable:true}));
        await sleep(150);
      }
    }
    await sleep(400);
  }
  async function openMenu(selector) {
    let button = document.querySelector(selector);
    if (!button || disabled(button)) fail('VIDEO_CONTROL_UNAVAILABLE', '视频设置按钮不可用。');
    const current = () => all('[role="menu"][data-state="open"]').find(m =>
      m.getAttribute('aria-labelledby') === document.querySelector(selector)?.id);
    if (current()) { await sleep(300); return current(); }
    await closeMenu();
    // Restoring editor focus can replace the action-bar buttons during cleanup.
    button = document.querySelector(selector);
    if (!button || disabled(button)) fail('VIDEO_CONTROL_UNAVAILABLE', '视频设置按钮不可用。');
    try {
      const menu = await until(() => {
        const opened = current();
        if (opened) return opened;
        const fresh = document.querySelector(selector);
        // Opening a closed settings menu is safe to repeat after focus cleanup.
        if (fresh?.getAttribute('aria-expanded') === 'false') toggle(fresh);
        return null;
      }, '展开菜单');
      // Radix installs dismissal/focus effects after mounting the portal.
      await sleep(300);
      return await until(current, '菜单就绪');
    } catch (error) {
      error.details = {control:selector, expanded:button.getAttribute('aria-expanded'), connected:button.isConnected};
      throw error;
    }
  }
  function current() {
    if (!active()) fail('VIDEO_MODE_REQUIRED', '当前未进入视频生成模式。');
    const model = text(document.querySelector(MODEL)).replace(/^模型\s*/, '');
    const match = text(document.querySelector(PARAMS)).match(/^(自动|\d+:\d+)\s*·\s*(\d+)s(?:\s*·|$)/);
    if (!model || !match) fail('VIDEO_LAYOUT_CHANGED', '无法回读视频模型、比例或秒数，请检查客户端布局。');
    return {model, ratio:match[1] === '自动' ? 'auto' : match[1], duration:Number(match[2])};
  }
  async function enter(route) {
    guard(route);
    if (active()) return;
    const entries = all('[data-testid="skill_bar_button_17"]').filter(e => text(e).includes('视频生成'));
    if (entries.length !== 1) fail('VIDEO_ENTRY_UNAVAILABLE', '未找到唯一的视频生成入口，请打开普通对话页面。');
    entries[0].click();
    await until(() => active() && document.querySelector(PARAMS) && text(document.querySelector(MODEL)), '进入视频模式');
    await sleep(150);
  }
  async function exit(route) {
    guard(route);
    if (active()) {
      await closeMenu();
      const button = all('[data-testid="skill_input_exit_button"]').at(-1);
      if (!button) fail('VIDEO_EXIT_UNAVAILABLE', '未找到退出视频模式按钮。');
      button.click();
      await until(() => !active(), '退出视频模式');
    }
    return {ok:true, background:true, mode:'chat', submitted:false};
  }
  function parameterData(menu) {
    const sections = all('section', menu);
    const ratioSection = sections.find(s => text(s.querySelector('div span')) === '比例');
    const durationSection = sections.find(s => text(s.querySelector('div span')) === '时长');
    const ratios = ratioSection ? all('button', ratioSection).map(e => ({
      value:text(e) === '自动' ? 'auto' : text(e), label:text(e), disabled:!!disabled(e)
    })) : [];
    const slider = durationSection?.querySelector('[role="slider"]');
    const seconds = [...text(durationSection).matchAll(/(\d+)\s*s/g)].map(m => Number(m[1]));
    const min = seconds.at(-2), max = seconds.at(-1);
    const indexMin = Number(slider?.getAttribute('aria-valuemin'));
    const indexMax = Number(slider?.getAttribute('aria-valuemax'));
    const index = Number(slider?.getAttribute('aria-valuenow'));
    if (!ratios.length || !slider || seconds.length < 3 || max < min || max-min > 300 ||
        ![min,max,indexMin,indexMax,index].every(Number.isInteger) || max-min !== indexMax-indexMin ||
        index < indexMin || index > indexMax || current().duration !== min+index-indexMin)
      fail('VIDEO_LAYOUT_CHANGED', '视频参数面板的比例或时长布局不受支持，未猜测控件值。');
    return {ratios, durations:Array.from({length:max-min+1}, (_,i) => min+i),
      duration_range:{min,max,step:1}, duration_disabled:!!disabled(slider), indexMin};
  }
  async function info(route) {
    guard(route);
    let models, parameters;
    try {
      const menu = await openMenu(MODEL);
      models = all('[role="menuitem"]', menu).map(e => ({
        value:text(e.querySelector('span')), label:text(e.querySelector('span')), disabled:!!disabled(e)
      })).filter(e => e.value);
      if (!models.length) fail('VIDEO_LAYOUT_CHANGED', '未读取到模型选项。');
      await closeMenu();
      parameters = parameterData(await openMenu(PARAMS));
    } finally { await closeMenu(); }
    guard(route);
    const {indexMin, ...options} = parameters;
    return {ok:true, background:true, mode:'video', submitted:false, current:current(), models, ...options};
  }
  async function selectModel(model, route) {
    guard(route);
    if (current().model === model) return;
    const menu = await openMenu(MODEL);
    const item = all('[role="menuitem"]', menu).find(e => text(e.querySelector('span')) === model);
    if (!item || disabled(item)) fail('VIDEO_MODEL_UNAVAILABLE', '当前客户端不提供或禁用了该模型：' + model);
    item.click();
    await until(() => text(document.querySelector(MODEL)).replace(/^模型\s*/, '') === model && !menu.isConnected, '切换模型');
    await sleep(150);
  }
  async function setParameters(ratio, duration, route) {
    guard(route);
    const menu = await openMenu(PARAMS);
    const data = parameterData(menu);
    const choice = data.ratios.find(o => o.value === ratio && !o.disabled);
    if (!choice || !data.durations.includes(duration) || data.duration_disabled)
      fail('VIDEO_OPTION_UNAVAILABLE', '当前模型不支持该比例或时长，请先查询视频选项。');
    const ratioButton = all('section button', menu).find(e => text(e) === choice.label);
    ratioButton.click();
    await until(() => current().ratio === ratio, '设置比例');
    if (current().duration !== duration) {
      const slider = () => all('[role="menu"][data-state="open"]').find(m =>
        m.getAttribute('aria-labelledby') === document.querySelector(PARAMS)?.id)?.querySelector('[role="slider"]');
      slider().dispatchEvent(new KeyboardEvent('keydown', {key:'Home', code:'Home', bubbles:true, cancelable:true}));
      await until(() => Number(slider()?.getAttribute('aria-valuenow')) === data.indexMin, '重置时长滑块');
      for (let i = 1; i <= duration-data.duration_range.min; i++) {
        guard(route);
        slider().dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight', code:'ArrowRight', bubbles:true, cancelable:true}));
        await until(() => Number(slider()?.getAttribute('aria-valuenow')) === data.indexMin+i, '设置时长滑块');
      }
    }
    await until(() => current().ratio === ratio && current().duration === duration, '回读视频参数');
    await closeMenu();
  }
  async function configure({model=null, ratio=null, duration=null} = {}) {
    const route = location.pathname, wasActive = active();
    let initial = null, changed = false;
    try {
      await enter(route);
      initial = await info(route);
      if (model !== null) {
        const choice = initial.models.find(o => o.value.toLowerCase() === model.trim().toLowerCase() && !o.disabled);
        if (!choice) fail('VIDEO_MODEL_UNAVAILABLE', '当前客户端不提供或禁用了该模型，请先查询视频选项。');
        model = choice.value;
        if (model !== initial.current.model) { changed = true; await selectModel(model, route); }
      }
      const available = changed ? await info(route) : initial;
      const wanted = {model:model ?? available.current.model, ratio:ratio ?? available.current.ratio,
        duration:duration ?? available.current.duration};
      if (!available.ratios.some(o => o.value === wanted.ratio && !o.disabled) ||
          !available.durations.includes(wanted.duration) || available.duration_disabled)
        fail('VIDEO_OPTION_UNAVAILABLE', '当前模型不支持该比例或时长，请查询当前模型的视频选项。');
      if (wanted.ratio !== available.current.ratio || wanted.duration !== available.current.duration) {
        changed = true;
        await setParameters(wanted.ratio, wanted.duration, route);
      }
      const result = await info(route);
      if (Object.keys(wanted).some(k => result.current[k] !== wanted[k]))
        fail('VIDEO_SETTING_MISMATCH', '视频参数回读不一致，设置未确认成功。');
      return result;
    } catch (error) {
      let restored = false;
      try {
        guard(route);
        await closeMenu();
        if (initial && changed) {
          await selectModel(initial.current.model, route);
          await setParameters(initial.current.ratio, initial.current.duration, route);
        }
        if (!wasActive && active()) await exit(route);
        restored = true;
      } catch {}
      error.details = {...error.details, rollback_succeeded:restored, settings_may_have_changed:!restored, submitted:false};
      throw error;
    }
  }
  return {active, current, configure, async options() {
    const route = location.pathname;
    await enter(route);
    return info(route);
  }, exit:() => exit(location.pathname)};
})();
