const fs=require('fs'),vm=require('vm'),assert=require('assert');
const script=fs.readFileSync('src/doubao_mcp/detail.js','utf8');
function fixture({task='t',panel='https://example/a?lr=unwatermarked',message='https://example/a?lr=unwatermarked',blocked=false,editable=false,missing=false,multiple=false,changeRoute=false}={}) {
 const video=url=>({currentSrc:url,readyState:4,duration:4,videoWidth:1280,videoHeight:720});
 const img={closest:()=>null,scrollIntoView(){},getBoundingClientRect:()=>({x:0,y:0,width:10,height:10})};
 const card={__reactFiber:{memoizedProps:{video:{creation_task_id:task,isSuccess:true}}},querySelector:()=>img,querySelectorAll:()=>[img],contains:x=>x===img};
 const cards=multiple?[card,{...card}]:[card];
 const m={isConnected:true,querySelectorAll:s=>s==='video'?[video(message)]:cards};
 const p={querySelectorAll:s=>s==='video'?[video(panel)]:editable?[{getBoundingClientRect:()=>({width:2,height:2})}]:[]};
 const document={querySelector:s=>s.includes('data-message-id')?(missing?null:m):s.includes('right-panel')?p:s.includes('dialog')?(blocked?{}:null):null,elementFromPoint:()=>img};
 const context={document,location:{pathname:'/chat/c'},CSS:{escape:x=>x},URL,requestAnimationFrame:f=>{if(changeRoute)context.location.pathname='/chat/new';f();},setTimeout};
 return vm.runInNewContext(script+'\ndetail',context);
}
(async()=>{
 let f=fixture();assert.equal((await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'})).already_open,true);
 f=fixture({panel:'https://example/b?lr=unwatermarked'});let r=await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'});assert.equal(r.already_open,false);assert.deepEqual(JSON.parse(JSON.stringify(r.point)),{x:5,y:5});
 f=fixture({task:'other'});assert.equal((await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'})).code,'RESOURCE_IDENTITY_UNVERIFIED');
 f=fixture({blocked:true});assert.equal((await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'})).code,'DETAIL_BLOCKED');
 f=fixture({editable:true});assert.equal((await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'})).code,'DETAIL_BLOCKED');
 f=fixture({missing:true});assert.equal((await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'})).code,'MESSAGE_NOT_LOADED');
 f=fixture({multiple:true});assert.equal((await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'})).code,'TARGET_AMBIGUOUS');
 f=fixture({panel:'https://example/b?lr=unwatermarked',changeRoute:true});assert.equal((await f({mode:'locate',message_id:'m',conversation_id:'c',task_id:'t'})).code,'CONVERSATION_CHANGED');
 console.log('detail DOM tests passed');
})().catch(e=>{console.error(e);process.exit(1)});
