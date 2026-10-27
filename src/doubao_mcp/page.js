// All actions stay inside the selected Doubao renderer. No native input or focus.
const doubao = (() => {
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  const fail = (code, message) => { throw Object.assign(new Error(message), {code}); };
  const norm = s => String(s ?? '').replace(/\r\n?/g, '\n').trim();
  function editor() {
    const nodes = all('[data-testid="chat_input_input"] [contenteditable="true"], [data-testid="chat_input_input"][contenteditable="true"], .ProseMirror')
      .filter(e => e.getClientRects().length);
    const unique = [...new Set(nodes)];
    if (unique.length !== 1) fail('EDITOR_UNAVAILABLE', '未找到唯一的豆包输入框，请完成登录并打开普通对话。');
    return unique[0];
  }
  function draft() {
    const e = editor();
    return norm('value' in e ? e.value : [...e.children].map(p => p.innerText).join('\n'));
  }
  function conversationId() {
    return location.pathname.match(/^\/chat\/(\d+)(?:\/|$)/)?.[1] ?? null;
  }
  function messages() {
    return all('[data-testid="message-list"] [data-message-role]').map(e => {
      const content = e.querySelector('[data-message-id]');
      if (!content) return null;
      const blocks = all('[data-testid="message_text_content"]', content);
      const text = blocks.length ? blocks.map(b => b.innerText).join('\n') : content.innerText;
      const streaming = !!e.querySelector('[data-streaming="true"]');
      return {id:content.dataset.messageId, role:e.dataset.messageRole, text:norm(text),
        streaming, complete:e.dataset.messageRole === 'assistant' && !streaming && !!e.querySelector('[data-testid="message_action_copy"]'),
        timestamp:e.querySelector('time')?.dateTime ?? null,
        has_non_text:!blocks.length,
        row:parseInt(e.closest('.v_list_row')?.style.getPropertyValue('--cls')?.match(/r-(\d+)/)?.[1] ?? '-1',10)};
    }).filter(Boolean);
  }
  function snapshot() {
    const items = messages();
    const last = items.at(-1);
    const stopping = !!document.querySelector('[data-testid="chat_input_stop_button"], [data-testid="stop_generation_button"], #flow-end-msg-stop');
    return {conversation_id:conversationId(), title:document.title.replace(/ - 豆包$/, ''), messages:items,
      generating:stopping || items.some(m=>m.streaming) || !!(last && (last.role==='user' || !last.complete))};
  }
  function idle() {
    if (snapshot().generating) fail('GENERATING', '豆包有未完成的回复，已停止，避免中断现有对话。');
    if (draft()) fail('DRAFT_PRESENT', '豆包输入框中已有草稿，已停止，避免覆盖或误发送。');
    const container = document.querySelector('[data-testid="chat_input"]');
    if (container?.querySelector('[data-testid*="attachment"], [data-testid*="uploaded_file"]'))
      fail('ATTACHMENT_PRESENT', '输入区有附件，请先手动处理后再发送文本。');
  }
  function sidebarScroller() {
    let e = document.querySelector('[data-testid="chat_list_wrapper"]');
    while (e && e !== document.body) {
      if (['auto','scroll'].includes(getComputedStyle(e).overflowY)) return e;
      e = e.parentElement;
    }
    return null;
  }
  function messageScroller() {
    return document.querySelector('[data-testid="message-list"] .scroller');
  }
  function scroll(e, position) {
    if (!e) return {available:false,top:0,height:0,total:0,bottom:true};
    if (position !== undefined) e.scrollTop = position === 'bottom' ? e.scrollHeight : position === 'top' ? 0 : position;
    return {available:true,top:e.scrollTop,height:e.clientHeight,total:e.scrollHeight,
      bottom:e.scrollTop+e.clientHeight>=e.scrollHeight-3};
  }
  return {
    status() {
      let available=false, hasDraft=null;
      try { hasDraft=!!draft(); available=true; } catch {}
      const s=snapshot();
      return {ok:true,running:true,background:true,transport:'cdp',editor_available:available,
        has_draft:hasDraft,generating:s.generating,conversation_id:s.conversation_id,title:s.title};
    },
    snapshot,
    draft:()=>({text:draft()}),
    check_idle:()=>{idle(); return {ok:true};},
    conversations() {
      const items=all('a[id^="conversation_"]').filter(e=>/^conversation_\d+$/.test(e.id)).map(e=>({
        id:e.id.slice('conversation_'.length),title:norm(e.innerText),
        active:e.classList.contains('e2e-test-active'),
        pinned:!!e.closest('[data-testid="pinned-section"]')}));
      return {items:[...new Map(items.map(e=>[e.id,e])).values()],scroll:scroll(sidebarScroller())};
    },
    scroll_sidebar:({position})=>scroll(sidebarScroller(),position),
    scroll_messages:({position})=>scroll(messageScroller(),position),
    new_chat() {
      idle();
      let buttons=all('[data-testid="create_conversation_button"] [role="button"]');
      if(!buttons.length) buttons=all('button[data-testid="create_conversation_button"]');
      if(buttons.length!==1) fail('NEW_CHAT_UNAVAILABLE','未找到唯一的新对话按钮。');
      buttons[0].click(); return {ok:true};
    },
    open({id}) {
      if(!/^\d+$/.test(id)) fail('INVALID_ID','对话 ID 必须为数字。');
      if(conversationId()===id) return {ok:true,already_open:true};
      idle();
      const link=document.getElementById('conversation_'+id);
      if(link) link.click();
      else setTimeout(()=>location.assign('/chat/'+id),0);
      return {ok:true};
    },
    focus_editor() {
      idle(); const e=editor(); e.focus({preventScroll:true});
      if(document.activeElement!==e && !e.contains(document.activeElement)) fail('EDITOR_FOCUS_FAILED','后台页面输入框未获得文档焦点。');
      return {ok:true};
    },
    submit({prompt}) {
      if(snapshot().generating) fail('GENERATING','豆包正在生成，未提交。');
      if(draft()!==norm(prompt)) fail('INPUT_MISMATCH','草稿与提示词不一致，未提交。');
      const buttons=all('[data-testid="chat_input_send_button"], #flow-end-msg-send');
      const unique=[...new Set(buttons)];
      if(unique.length!==1 || unique[0].disabled || unique[0].getAttribute('aria-disabled')==='true' || unique[0].dataset.disabled==='true')
        fail('SEND_UNAVAILABLE','发送按钮不可用，草稿已保留。');
      unique[0].click(); return {ok:true};
    },
    inspect() {
      return {ok:true,url:location.origin+location.pathname,background:true,
        testids:[...new Set(all('[data-testid]').map(e=>e.dataset.testid))],
        editors:all('[contenteditable]').map(e=>({class:e.className,editable:e.contentEditable})),
        message_count:messages().length};
    }
  };
})();
