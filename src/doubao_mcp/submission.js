// Final video send is separate from preparation and guarded by a durable Python ledger.
const doubaoSubmission = (() => {
  const fail = (code, message) => { throw Object.assign(new Error(message), {code}); };
  const visible = element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden';
  function sendButton() {
    const buttons = [...new Set([...document.querySelectorAll('[data-testid="chat_input_send_button"], #flow-end-msg-send')])].filter(visible);
    if (buttons.length !== 1 || buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true' || buttons[0].dataset.disabled === 'true')
      fail('SEND_UNAVAILABLE', '发送按钮不可用，已保留提示词和引用。');
    return buttons[0];
  }
  async function verify({prompt, asset_ids, model, ratio, duration}) {
    if (!doubaoVideo.active()) fail('VIDEO_MODE_REQUIRED', '请先进入视频模式并设置参数。');
    const current = doubaoVideo.current();
    if (current.model !== model || current.ratio !== ratio || current.duration !== duration)
      fail('VIDEO_SETTING_MISMATCH', '界面的视频模型、比例或秒数与提交要求不一致，未发送。');
    if ([...document.querySelectorAll('[data-testid="chat_input_stop_button"], [data-testid="stop_generation_button"], [data-testid="chat_input_local_break_button"], #flow-end-msg-stop')].some(visible) ||
        document.querySelector('[data-testid="message-list"] [data-streaming="true"]'))
      fail('GENERATING', '当前有回复正在生成，未提交视频。');
    const media = await doubaoMedia.verify({prompt, asset_ids});
    sendButton();
    const proof = {route:location.pathname, current, draft_text:media.draft_text,
      assets:media.assets.map(a=>({asset_id:a.asset_id,resource_id:a.resource_id,uploaded:a.uploaded})).sort((a,b)=>a.asset_id.localeCompare(b.asset_id)),
      bindings:media.bindings.map(b=>({asset_id:b.asset_id,resource_id:b.resource_id,label:b.label,attrs:b.node?.attrs}))};
    return {ok:true, verified:true, submitted:false, proof, media,
      conversation_id:location.pathname.match(/^\/chat\/(\d+)/)?.[1] ?? null};
  }
  function confirmVerify({prompt, conversation_id, confirmation_message_id}) {
    if (location.pathname !== '/chat/'+conversation_id || doubaoVideo.active())
      fail('REQUEST_CONTEXT_CHANGED', '确认前原对话或输入模式发生变化，未发送。');
    const state = doubao.snapshot(), last = state.messages.at(-1);
    if (state.generating || !last || last.role !== 'assistant' || last.id !== confirmation_message_id)
      fail('CONFIRMATION_CHANGED', '原参数确认消息已变化，未发送。');
    const media = doubaoMedia.verify({prompt,asset_ids:[]});
    sendButton();
    return {ok:true, verified:true, proof:{route:location.pathname,draft_text:media.draft_text,confirmation_message_id}};
  }
  return {verify, confirmVerify, async confirmSend(request) {
    const check = confirmVerify(request);
    if (JSON.stringify(check.proof) !== JSON.stringify(request.proof))
      fail('CONFIRMATION_CHANGED', '确认发送前草稿或原消息变化，未发送。');
    sendButton().click();
    return {ok:true,clicked:true,conversation_id:request.conversation_id};
  }, async send(request) {
    const check = await verify(request);
    if (JSON.stringify(check.proof) !== JSON.stringify(request.proof))
      fail('VIDEO_PREFLIGHT_CHANGED', '提交前界面或图片绑定发生变化，未发送。');
    sendButton().click();
    return {ok:true, clicked:true, submitted:true, conversation_id:check.conversation_id};
  }};
})();
