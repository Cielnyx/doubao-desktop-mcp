// Read rendered task cards only. No submission, browser storage, or credentials.
const doubaoTasks = (() => {
  const all = (selector, root=document) => [...root.querySelectorAll(selector)];
  const text = node => (node?.innerText || node?.textContent || '').trim();
  const id = value => {
    if (typeof value === 'number' && !Number.isSafeInteger(value)) return null;
    const result = typeof value === 'string' || typeof value === 'number' || typeof value === 'bigint' ? String(value) : '';
    return /^[A-Za-z0-9_:\-]{1,200}$/.test(result) ? result : null;
  };
  const positive = value => Number.isFinite(Number(value)) && Number(value)>0 ? Number(value) : null;
  function messageMeta(node) {
    let fiber=node?.[Object.keys(node).find(k=>k.startsWith('__reactFiber'))];
    for(let depth=0; fiber && depth<15; depth++,fiber=fiber.return) {
      const message=fiber.memoizedProps?.message;
      if(!message || id(message.message_id)!==node.dataset.messageId)continue;
      let ability=null,parameters=null,creation=[];
      try {ability=typeof message.ext?.chat_ability==='string'?JSON.parse(message.ext.chat_ability):message.ext?.chat_ability;
        parameters=typeof ability?.ability_param==='string'?JSON.parse(ability.ability_param):ability?.ability_param;}catch{}
      try {creation=JSON.parse(message.ext?.ai_creation_duration_info??'[]');}catch{}
      const original=parameters?.input_box_content?.user_input_content;
      const source=typeof original==='string'?original.slice(0,24000):null;
      const blocks=message.content_blocks_v2??message.content_block??[];
      const rawText=Array.isArray(blocks)?blocks.map(b=>b.content?.text_block?.text).filter(v=>typeof v==='string').join('\n'):'';
      const assets=source?[...source.matchAll(/attachment:\/\/([^?\s)]+)\?type=\d+/g)].map(m=>m[1]):[];
      const index=Number(message.index);
      return {source:'react.message',index:Number.isSafeInteger(index)?index:null,
        reply_id:message.reply_id==='0'?null:id(message.reply_id),primary_message_id:id(message.ext?.chat_id),
        original_input:source,raw_text:rawText.slice(0,24000),input_asset_ids:[...new Set(assets)],
        configured:parameters?{model:parameters.model??null,ratio:parameters.ratio??null,duration:parameters.duration??null}:null,
        confirmation_required:message.ext?.is_creation_clarifying==='1',
        task_ids:Array.isArray(creation)?creation.map(item=>id(item.task_id)).filter(Boolean):[]};
    }
    return null;
  }
  function model(raw) {
    try {
      const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (!value || typeof value !== 'object') return null;
      return {status:value.status ?? null, video_duration:positive(value.video_duration),
        video_list:Object.fromEntries(Object.entries(value.video_list ?? {}).slice(0,30).map(([key,v]) => [key,{
          vtype:v.vtype, codec_type:v.codec_type, encryption_method:v.encryption_method,
          vwidth:positive(v.vwidth), vheight:positive(v.vheight), size:positive(v.size), main_url:v.main_url
        }]))};
    } catch { return null; }
  }
  function cardProps(node) {
    let fiber = node?.[Object.keys(node).find(k=>k.startsWith('__reactFiber'))];
    for(let depth=0; fiber && depth<18; depth++, fiber=fiber.return) {
      const props = fiber.memoizedProps;
      const value = props?.video;
      if (value && typeof value === 'object' && (value.creation_task_id != null || value.videoModel != null)) {
        return {task_id:id(value.creation_task_id), task_id_source:'react.video.creation_task_id',
          is_success:typeof value.isSuccess === 'boolean' ? value.isSuccess : null,
          status_code:value.status ?? value.task_status ?? null,
          video_model:model(value.videoModel)};
      }
    }
    return null;
  }
  function safeUrl(raw) {
    try { const value = new URL(raw); return ['http:','https:'].includes(value.protocol) ? value.href : null; }
    catch { return null; }
  }
  function cardVideos(card, messageId) {
    const result=[];
    if (card?.is_success !== true) return result;
    for (const [quality,stream] of Object.entries(card.video_model?.video_list ?? {})) {
      if (!stream.main_url || stream.encryption_method || (stream.vtype && stream.vtype !== 'mp4')) continue;
      let url=safeUrl(stream.main_url);
      if (!url) { try { url=safeUrl(atob(stream.main_url)); } catch {} }
      if (url) result.push({url, source:'client_card_metadata', task_id:card.task_id,
        message_id:messageId, duration:card.video_model.video_duration, width:stream.vwidth,
        height:stream.vheight, expected_bytes:stream.size, codec:stream.codec_type ?? null, quality});
    }
    return result.sort((a,b)=>(b.codec==='h264')-(a.codec==='h264') ||
      (b.width??0)*(b.height??0)-(a.width??0)*(a.height??0));
  }
  function rows() {
    const result=[];
    for (const row of all('[data-testid="message-list"] [data-message-role]')) {
      const content = row.querySelector('[data-message-id]');
      if (!content) continue;
      const messageId=id(content.dataset.messageId);
      if (!messageId) continue;
      const blocks=all('[data-testid="message_text_content"]',content);
      const body=blocks.length ? blocks.map(text).join('\n') : text(content);
      const metadata=messageMeta(content);
      const virtualRow=row.closest('.v_list_row');
      const rowIndex=Number(virtualRow?.style.getPropertyValue('--cls').match(/r-(\d+)/)?.[1]);
      const scroller=row.closest('.scroller');
      const position=row.getBoundingClientRect().top+(scroller?.scrollTop??0);
      const base={message_id:messageId,role:row.dataset.messageRole,text:body.slice(0,20000),
        streaming:!!row.querySelector('[data-streaming="true"]'),message_meta:metadata,
        display_order:metadata?.index??(Number.isInteger(rowIndex)?rowIndex:position)};
      const cardNodes=all('[class*="block-video-"],[data-testid*="video_gen"],[data-testid*="video-generation"]',content);
      const cards=cardNodes.map(cardProps).filter(Boolean);
      const distinct=[...new Map(cards.map((card,index)=>[card.task_id ?? 'unidentified-'+index,card])).values()];
      if (!distinct.length) distinct.push(null);
      const players=all('video',content).map(v=>({url:safeUrl(v.currentSrc||v.src),source:'rendered_video_player',
        message_id:messageId,duration:positive(v.duration),width:positive(v.videoWidth),height:positive(v.videoHeight),
        ready_state:v.readyState})).filter(v=>v.url);
      for (const card of distinct) {
        const taskIds=[];
        if(card?.task_id)taskIds.push({id:card.task_id,source:card.task_id_source});
        if(base.role==='assistant')for(const task of metadata?.task_ids??[]) {
          if(!taskIds.some(item=>item.id===task))taskIds.push({id:task,source:'react.message.ext.ai_creation_duration_info.task_id'});
        }
        for(const node of [content,...all('[data-task-id],[data-creation-task-id],[data-generation-task-id]',content)]) {
          for(const key of ['data-task-id','data-creation-task-id','data-generation-task-id']) {
            const value=id(node.getAttribute(key));
            if(value && !taskIds.some(item=>item.id===value))taskIds.push({id:value,source:'dom.'+key});
          }
        }
        const videos=cardVideos(card,messageId);
        if(distinct.length===1)videos.push(...players.map(v=>({...v,task_id:card?.task_id ?? null})));
        result.push({...base,task_ids:taskIds,card,videos:[...new Map(videos.map(v=>[v.url,v])).values()]});
      }
    }
    return result;
  }
  async function scan({message_id=null,task_id=null,scan_history=true}={}) {
    const route=location.pathname;
    const conversationId=route.match(/^\/chat\/(\d+)(?:\/|$)/)?.[1] ?? null;
    const scroller=document.querySelector('[data-testid="message-list"] .scroller');
    const originalTop=scroller?.scrollTop;
    const collected=new Map();
    let complete=!scroller,stable=0,previous=null;
    const deadline=Date.now()+16000;
    try {
      if(scan_history && scroller && !rows().some(r=>message_id ? r.message_id===message_id : task_id ? r.task_ids.some(t=>t.id===task_id) : false)) { scroller.scrollTop=scroller.scrollHeight; await new Promise(r=>setTimeout(r,200)); }
      while(true) {
        if(location.pathname!==route)throw Object.assign(new Error('查询任务期间对话发生变化。'),{code:'CONVERSATION_CHANGED'});
        for(const record of rows())collected.set(record.message_id+':'+(record.card?.task_id ?? ''),record);
        const found=[...collected.values()].some(r=>message_id ? r.message_id===message_id : task_id ? r.task_ids.some(t=>t.id===task_id) : false);
        if(!scan_history || !scroller || found)break;
        const key=[scroller.scrollTop,scroller.scrollHeight,collected.size].join(':');
        stable=key===previous ? stable+1 : 0;
        previous=key;
        if(scroller.scrollTop<=2 && stable>=3){complete=true;break;}
        if(Date.now()>=deadline)break;
        scroller.scrollTop=Math.max(0,scroller.scrollTop-Math.max(100,scroller.clientHeight*.8));
        await new Promise(r=>setTimeout(r,200));
      }
    } finally { if(scroller && location.pathname===route)scroller.scrollTop=originalTop; }
    // Message IDs are opaque: an assistant ID can be numerically smaller than
    // its preceding user ID. Use the renderer's message index/virtual row order.
    const records=[...collected.values()].sort((a,b)=>a.display_order-b.display_order);
    return {ok:true,conversation_id:conversationId,records,scan_complete:complete,
      generating_control:!!document.querySelector('[data-testid="chat_input_local_break_button"],[data-testid="chat_input_stop_button"],[data-testid="stop_generation_button"]')};
  }
  return {scan, completedMessage(messageId) {
    return rows().some(row => row.message_id === messageId &&
      (row.card?.is_success === true || row.videos.some(v => v.url && v.duration > 0 && (v.ready_state ?? 1) >= 1)));
  }};
})();
