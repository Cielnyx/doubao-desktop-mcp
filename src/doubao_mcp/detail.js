// Operates only on a validated completed message; Python validates the task card.
const detail = async ({mode,message_id,conversation_id,task_id}) => {
 const fail=(code,message)=>({ok:false,code,message});
 if(location.pathname!==`/chat/${conversation_id}`)return fail('CONVERSATION_CHANGED','对话已变化。');
 const msg=()=>document.querySelector(`[data-message-id="${CSS.escape(message_id)}"]`);
 const inspect=()=>{
  const m=msg(), panel=document.querySelector('[data-testid="right-panel-container"]');
  const pv=[...(panel?.querySelectorAll('video')||[])].map(v=>({url:v.currentSrc||v.src,ready_state:v.readyState,duration:Number.isFinite(v.duration)?v.duration:null,width:v.videoWidth,height:v.videoHeight})).filter(v=>v.url);
  const mv=[...(m?.querySelectorAll('video')||[])].map(v=>v.currentSrc||v.src).filter(Boolean);
  const matches=pv.filter(v=>mv.includes(v.url)&&v.ready_state>=1&&new URL(v.url).searchParams.get('lr')==='unwatermarked');
  return {panel_present:!!panel,message_present:!!m,matches};
 };

 const panel=document.querySelector('[data-testid="right-panel-container"]');
 const editable=panel && [...panel.querySelectorAll('textarea,input:not([type=range]),[contenteditable="true"]')].some(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&!e.disabled;});
 if(mode!=='inspect' && editable)return fail('DETAIL_BLOCKED','详情面板存在编辑内容，未替换。');
 if(mode!=='inspect' && document.querySelector('[role="dialog"],[aria-modal="true"]'))return fail('DETAIL_BLOCKED','存在对话框或遮挡层，未操作。');
 const scroller=document.querySelector('[data-testid="message-list"] .scroller');
 if(!msg()&&scroller){scroller.scrollTop=scroller.scrollHeight;let previous='';let stable=0;const end=Date.now()+16000;
  while(!msg()&&Date.now()<end){await new Promise(r=>setTimeout(r,200));const key=[scroller.scrollTop,scroller.scrollHeight].join(':');stable=key===previous?stable+1:0;previous=key;if(scroller.scrollTop<=2&&stable>=3)break;scroller.scrollTop=Math.max(0,scroller.scrollTop-Math.max(100,scroller.clientHeight*.8));}}
 const m=msg();if(!m)return fail('MESSAGE_NOT_LOADED','未加载目标消息。');
 const liveCards=[...m.querySelectorAll('[class*="block-video-"]')].map(node=>{let f=node[Object.keys(node).find(k=>k.startsWith('__reactFiber'))];for(let n=0;f&&n<18;n++,f=f.return){let v=f.memoizedProps?.video;if(v?.creation_task_id!=null)return {id:String(v.creation_task_id),success:v.isSuccess};}return null;}).filter(Boolean);
 const ids=[...new Set(liveCards.map(c=>c.id))];
 if(ids.length!==1||ids[0]!==task_id||liveCards.some(c=>c.success!==true))return fail('RESOURCE_IDENTITY_UNVERIFIED','实时卡片任务身份无法确认。');
 const cards=[...m.querySelectorAll('[class*="block-video-"]')].filter(x=>x.querySelector('img'));
 const roots=cards.filter(x=>!cards.some(y=>y!==x&&y.contains(x)));
 if(roots.length!==1)return fail('TARGET_AMBIGUOUS','目标消息的视频卡片不唯一。');
 const prior=inspect();if(mode==='inspect')return {ok:true,...prior};if(prior.matches.length===1)return {ok:true,already_open:true,...prior};
 const images=[...roots[0].querySelectorAll('img')].filter(i=>!i.closest('button,[role="button"],[data-testid*="canvas"]'));
 if(images.length!==1)return fail('TARGET_AMBIGUOUS','目标封面不唯一。');
 const img=images[0];img.scrollIntoView({block:'center'});await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 if(location.pathname!==`/chat/${conversation_id}`||msg()!==m||!m.isConnected)return fail('CONVERSATION_CHANGED','目标消息已变化。');
 const currentIds=[...m.querySelectorAll('[class*="block-video-"]')].map(node=>{let f=node[Object.keys(node).find(k=>k.startsWith('__reactFiber'))];for(let n=0;f&&n<18;n++,f=f.return){let v=f.memoizedProps?.video;if(v?.creation_task_id!=null)return String(v.creation_task_id);}return null;}).filter(Boolean);
 if(!currentIds.length||currentIds.some(id=>id!==task_id))return fail('RESOURCE_IDENTITY_UNVERIFIED','任务卡片已变化。');
 const rect=img.getBoundingClientRect(),x=rect.x+rect.width/2,y=rect.y+rect.height/2,hit=document.elementFromPoint(x,y);
 if(!rect.width||!rect.height||!hit||!roots[0].contains(hit)||hit.closest('button,[role="button"],[data-testid*="canvas"]'))return fail('DETAIL_BLOCKED','封面被遮挡或命中操作按钮。');
 return {ok:true,already_open:false,point:{x,y},before:prior};
};
