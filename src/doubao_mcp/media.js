// Uploaded resources and mentions are read from the current input, never account storage.
const doubaoMedia = (() => {
  const all = (selector, root=document) => [...root.querySelectorAll(selector)];
  const fail = (code,message,details={}) => { throw Object.assign(new Error(message),{code,details}); };
  const normalise = value => String(value ?? '').replace(/\r\n?/g,'\n');
  const VIDEO_PROMPT_RULES = '请直接开始生成，不要再次询问是否需要生成或要求确认。严格按照以下用户提示词和已绑定参考图片执行，不改写、不扩写、不擅自增删内容；模型、比例和时长以本次已选参数为准。';
  function editor() {
    const nodes=all('.ProseMirror[contenteditable="true"]').filter(e=>e.getClientRects().length);
    if(nodes.length!==1 || !nodes[0].editor?.getJSON) fail('MEDIA_EDITOR_UNAVAILABLE','无法读取唯一的豆包富文本编辑器。');
    return nodes[0].editor;
  }
  function context() {
    return {route:location.pathname,input_instance_id:document.querySelector('[data-testid="chat_input"]')?.getAttribute('data-input-engine-instance-id')??null};
  }
  function guard(expected=null) {
    if(expected && (expected.route!==context().route || expected.input_instance_id!==context().input_instance_id))
      fail('MEDIA_CONTEXT_CHANGED','上传或绑定期间当前对话输入区发生变化，已停止。');
    if(doubao.snapshot().generating) fail('GENERATING','豆包有未完成的回复，已停止图片操作。');
  }
  function attachmentStates() {
    const element=document.querySelector('[data-testid="attachment_area"]') || document.querySelector('[data-testid="attachment-image-card"]');
    let fiber=element?.[Object.keys(element).find(k=>k.startsWith('__reactFiber'))];
    for(let i=0;fiber && i<24;i++,fiber=fiber.return) {
      if(Array.isArray(fiber.memoizedProps?.attachmentStates)) return fiber.memoizedProps.attachmentStates;
    }
    if(document.querySelector('[data-testid="attachment-image-card"]'))
      fail('MEDIA_LAYOUT_CHANGED','有图片附件，但无法验证客户端实际上传状态。');
    return [];
  }
  function assets() {
    return attachmentStates().filter(a=>a.type==='image').map(a=>({
      asset_id:a.fileKey || a.localKey || null, resource_id:a.fileKey || null,
      local_key:a.localKey || null, name:a.fileName || '', status:a.status || null,
      upload_percent:a.uploadPercent ?? null,
      uploaded:a.status==='Normal' && Number(a.uploadPercent)===100 && !!a.fileKey,
      width:a.imageWidth ?? null,height:a.imageHeight ?? null,md5:a.md5 || null,size:a.size ?? null
    }));
  }
  function documentData(json, available) {
    const bindings=[],unresolved=[];
    function render(node) {
      if(node.type==='text') return normalise(node.text);
      if(node.type==='inputEngineMention') {
        const attrs=node.attrs ?? {}, resource=attrs.resourceId, asset=available.find(a=>a.resource_id===resource);
        const item={asset_id:asset?.asset_id??null,resource_id:resource??null,label:attrs.label??'',node};
        if(!asset || !asset.uploaded || attrs.id!==resource || attrs.mentionType!=='file' ||
           attrs.metadata?.resourceType!=='image' || attrs.metadata?.locatorKind!=='file-key' || attrs.label!==asset.name)
          unresolved.push(item);
        else bindings.push(item);
        return '@'+String(attrs.label ?? '');
      }
      if(node.type==='hardBreak') return '\n';
      if(!['doc','paragraph'].includes(node.type)) fail('MEDIA_EDITOR_LAYOUT_CHANGED','输入区包含不支持的富文本节点：'+node.type);
      const values=(node.content??[]).map(render);
      return values.join(node.type==='doc'?'\n':'');
    }
    return {draft_text:render(json),bindings,unresolved_mentions:unresolved};
  }
  function inspect() {
    const available=assets(), richEditor=editor(), json=richEditor.getJSON();
    return {ok:true,context:context(),editor_json:json,assets:available,
      visible_text:normalise(richEditor.view.dom.innerText),serialized_text:normalise(richEditor.getText()),
      ...documentData(json,available)};
  }
  function requireAssets(assetIds, state) {
    if(!Array.isArray(assetIds) || new Set(assetIds).size!==assetIds.length)
      fail('INVALID_IMAGE_ASSETS','图片 asset_ids 应为不重复的数组。');
    return assetIds.map(id=>{
      const matches=state.assets.filter(a=>a.asset_id===id);
      if(matches.length!==1 || !matches[0].uploaded) fail('IMAGE_NOT_UPLOADED','图片不在当前对话或尚未上传完成：'+id);
      return matches[0];
    });
  }
  function verify({prompt,asset_ids}) {
    const state=inspect();
    requireAssets(asset_ids,state);
    if(state.draft_text!==normalise(prompt)) fail('VIDEO_PROMPT_MISMATCH','完整提示词回读不一致，未提交。');
    if(state.unresolved_mentions.length) fail('UNRESOLVED_IMAGE_REFERENCE','存在未解析的图片引用，未提交。');
    const expected=[...asset_ids].sort(), actual=[...new Set(state.bindings.map(b=>b.asset_id))].sort();
    if(JSON.stringify(actual)!==JSON.stringify(expected)) fail('IMAGE_BINDING_MISMATCH','结构化图片引用与所要求的图片资源不一致，未提交。');
    if(state.assets.some(a=>!a.uploaded) || JSON.stringify([...new Set(state.assets.map(a=>a.asset_id))].sort())!==JSON.stringify(expected))
      fail('UNBOUND_IMAGE_ATTACHMENT','当前存在未完成上传或未绑定的额外图片附件，未提交。');
    return {...state,verified:true};
  }
  function candidateResource(element) {
    let fiber=element?.[Object.keys(element).find(k=>k.startsWith('__reactFiber'))];
    for(let i=0;fiber && i<5;i++,fiber=fiber.return) {
      const key=typeof fiber.key==='string'?fiber.key:'';
      const marker=':current-attachment:';
      if(key.includes(marker)) return key.slice(key.indexOf(marker)+marker.length);
    }
    return null;
  }
  function candidates() {
    return {ok:true,candidates:all('[role="option"][data-mention-option-index]').map(element=>({
      label:element.textContent.trim(),resource_id:candidateResource(element),
      index:element.getAttribute('data-mention-option-index'),
      disabled:element.getAttribute('aria-disabled')==='true'
    }))};
  }
  function uploadTarget() {
    guard();
    const selector='[data-testid="chat_input"] input[data-testid="upload-file-input"][type="file"]';
    const inputs=all(selector);
    if(inputs.length!==1 || inputs[0].disabled) fail('IMAGE_UPLOAD_UNAVAILABLE','当前对话没有唯一可用的图片上传控件。');
    return {...inspect(),selector,accept:inputs[0].accept,multiple:inputs[0].multiple};
  }
  function beginBind({asset_id}) {
    guard();
    const state=inspect();
    const [asset]=requireAssets([asset_id],state);
    if(state.unresolved_mentions.length) fail('UNRESOLVED_IMAGE_REFERENCE','输入区已有无法解析的引用，请先处理。');
    if(state.bindings.some(b=>b.asset_id===asset_id)) return {...state,already_bound:true};
    if(state.draft_text.endsWith('@') && candidates().candidates.some(c=>c.resource_id===asset_id))
      return {ok:true,context:state.context,asset,before_json:state.editor_json,already_bound:false,trigger_already_present:true};
    editor().commands.focus('end',{scrollIntoView:false});
    return {ok:true,context:state.context,asset,before_json:state.editor_json,already_bound:false,
      insert_text:state.draft_text && !/\s$/.test(state.draft_text)?' @':'@'};
  }
  function selectCandidate({asset_id,expected_context}) {
    guard(expected_context);
    const [asset]=requireAssets([asset_id],inspect());
    const matches=all('[role="option"][data-mention-option-index]').filter(e=>candidateResource(e)===asset.resource_id);
    if(matches.length!==1 || matches[0].getAttribute('aria-disabled')==='true')
      fail('IMAGE_CANDIDATE_UNAVAILABLE','没有找到唯一对应真实图片资源的 @ 候选。',{candidates:candidates()});
    if(matches[0].textContent.trim()!==asset.name) fail('IMAGE_CANDIDATE_MISMATCH','图片候选标签与实际附件不一致。');
    matches[0].click();
    return {ok:true,selected_resource_id:asset.resource_id};
  }
  function prepare({prompt,asset_ids,source_prompt=null}) {
    guard();
    if(typeof prompt!=='string' || !prompt.trim() || prompt.length>20000 || prompt.includes('\0'))
      fail('INVALID_PROMPT','完整视频提示词必须是1–20000字符的非空字符串。');
    prompt=normalise(prompt);
    if(source_prompt!==null) {
      if(typeof source_prompt!=='string' || !source_prompt.trim() || source_prompt.length>20000 || source_prompt.includes('\0'))
        fail('INVALID_SOURCE_PROMPT','受控替换的原提示词必须是有效完整原文。');
      source_prompt=normalise(source_prompt);
      const marker='\n'+VIDEO_PROMPT_RULES+'\n用户提示词：\n';
      const index=prompt.indexOf(marker), head=prompt.slice(0,index);
      if(index<0 || !/^帮我用[^\r\n]+生成视频。$/.test(head) || prompt.slice(index+marker.length)!==source_prompt)
        fail('INVALID_SOURCE_PROMPT','受控替换只允许给同一份用户原文添加系统生成指令。');
    }
    const state=inspect(), selected=requireAssets(asset_ids,state);
    if(state.unresolved_mentions.length) fail('UNRESOLVED_IMAGE_REFERENCE','草稿存在未解析引用。');
    const templates=new Map();
    for(const asset of selected) {
      if(selected.filter(a=>a.name===asset.name).length!==1 || state.assets.filter(a=>a.name===asset.name).length!==1)
        fail('AMBIGUOUS_IMAGE_LABEL','当前有同名图片，不能按文件名猜测 @ 引用，请重命名图片后重新上传。');
      const binding=state.bindings.find(b=>b.asset_id===asset.asset_id);
      if(!binding) fail('IMAGE_NOT_BOUND','图片尚未通过实际 @ 候选绑定：'+asset.name);
      templates.set(asset.name,binding.node);
    }
    const onlyRefs=node=>node.type==='text'? !node.text.trim() : node.type==='inputEngineMention' || (node.content??[]).every(onlyRefs);
    const sourceMatches=source_prompt!==null && state.draft_text===source_prompt;
    if(state.draft_text!==prompt && !onlyRefs(state.editor_json) && !sourceMatches)
      fail('DRAFT_PRESENT','输入框已有其他提示词，未覆盖现有草稿。');
    const labels=[...templates.keys()].sort((a,b)=>b.length-a.length);
    const used=new Set();
    const paragraphs=prompt.split('\n').map(line=>{
      const content=[];let textBuffer='';
      const flush=()=>{if(textBuffer){content.push({type:'text',text:textBuffer});textBuffer='';}};
      for(let i=0;i<line.length;) {
        const label=line[i]==='@'?labels.find(label=>line.startsWith('@'+label,i) && !/[A-Za-z0-9_.-]/.test(line[i+label.length+1]??'')):null;
        if(label){flush();content.push(JSON.parse(JSON.stringify(templates.get(label))));used.add(label);i+=label.length+1;}
        else{textBuffer+=line[i];i++;}
      }
      flush();return content.length?{type:'paragraph',content}:{type:'paragraph'};
    });
    for(const label of labels) if(!used.has(label)) fail('IMAGE_REFERENCE_MISSING','完整提示词必须包含真实图片占位符 @'+label);
    for(const match of prompt.matchAll(/@([^\s@]+?\.(?:png|jpe?g|webp|apng))(?![A-Za-z0-9_.-])/gi))
      if(!templates.has(match[1])) fail('UNBOUND_IMAGE_REFERENCE','提示词含有未绑定的图片引用：@'+match[1]);
    editor().commands.setContent({type:'doc',content:paragraphs},{emitUpdate:true});
    return verify({prompt,asset_ids});
  }
  return {inspect,verify,candidates,uploadTarget,beginBind,selectCandidate,prepare};
})();
