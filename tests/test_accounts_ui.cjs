// Run with node tests/test_accounts_ui.cjs. Synthetic DOM; no real account changes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../src/doubao_mcp/accounts.js'),'utf8');

function environment(count=3) {
  const state = {open:false, submenu:false, busy:null, clicks:[], dialogs:[], route:'/chat/1', disabled:false};
  const profiles = Array.from({length:count},(_,i)=>({entity_id:String(101+i), nickname:i?'同名':'主账号',sec_id:'private-profile-data'}));
  const user = {user_id_str:'101',name:'主账号',session_key:'private-session-data'};
  function chain(values) { return values.reduceRight((next,memoizedState)=>({memoizedState,next}),null); }
  const root={stateNode:{}};root.stateNode.current=root;
  const avatarFiber = {type:function Avatar(){},memoizedState:chain([user]),return:root};
  function AccountMenu(){ return {multiUserProfile:null,handleSwitchAccount:null}; }
  const menuFiber = {type:AccountMenu,memoizedState:chain([profiles,{current:profiles}]),return:root};
  function node(attributes={}) {
    return {attributes,textContent:'',getClientRects:()=>[{}],
      getAttribute(key){return this.attributes[key]??null;},
      hasAttribute(key){return key in this.attributes;},
      dispatchEvent(event){if(event.key==='Escape'){if(state.submenu)state.submenu=false;else state.open=false;}return true;}};
  }
  const button = node();
  button.getAttribute = key => key==='aria-expanded'?String(state.open):null;
  button.dispatchEvent = event => {if(event.type==='pointerdown')state.open=true;return true;};
  const avatar = node(); avatar.closest = ()=>button; avatar.__reactFiber$test=avatarFiber;
  const menu = node(); menu.getClientRects = ()=>state.open?[{}]:[];
  const add = node(); add.__reactFiber$test={return:menuFiber};
  const rows = profiles.map((p,index)=>{
    const row = node();
    row.__reactFiber$test={memoizedProps:{profile:p,curUid:'101',handleSwitchAccount(){},loading:false},return:root};
    row.getClientRects=()=>state.open&&(index<2||state.submenu)?[{}]:[];
    row.hasAttribute = key => key==='data-disabled'&&state.disabled;
    row.click=()=>{state.clicks.push(p.entity_id);state.open=false;user.user_id_str=p.entity_id;user.name=p.nickname;};
    return row;
  });
  const more=node(); more.textContent='更多账号'; more.click=()=>{state.submenu=true;state.onMore?.();};
  const document = {
    activeElement:menu,
    querySelector(selector){
      if(selector.includes('chat_header_avatar_button'))return avatar;
      if(selector.includes('chat_header_menu'))return state.open?menu:null;
      if(selector.includes('avatar_menu_add_account'))return state.open?add:null;
      if(selector.includes('avatar_menu_account_row'))return state.open?rows[0]:null;
      if(selector.includes('chat_input_input'))return node();
      return null;
    },
    querySelectorAll(selector){
      if(selector.includes('avatar_menu_account_row'))return rows;
      if(selector.includes('dropdown-menu-sub-trigger'))return [more];
      if(selector.includes('dialog'))return state.dialogs;
      return [];
    },
  };
  const location={get pathname(){return state.route;}};
  class Event{constructor(type,fields){this.type=type;Object.assign(this,fields);}}
  const context=vm.createContext({document,location,PointerEvent:Event,KeyboardEvent:Event,
    setTimeout:fn=>{fn();},doubao:{check_idle(){if(state.busy)throw Object.assign(new Error('busy'),{code:state.busy});}}});
  vm.runInContext(source+'\nglobalThis.api=doubaoAccounts;',context);
  return {api:context.api,state,profiles,user,rows,avatar,root,avatarFiber};
}

async function main(){
  let passed=0;
  async function test(name,run){await run();passed++;process.stdout.write(`ok ${passed} - ${name}\n`);}
  await test('all saved profiles returned; private fields excluded; owned menu closed',async()=>{
    const {api,state}=environment(); const data=await api.list();
    assert.equal(data.accounts.length,3); assert.equal(state.open,false);
    assert.equal(JSON.stringify(data).includes('private-'),false);
  });
  await test('a previously open menu stays open after listing',async()=>{
    const {api,state}=environment();state.open=true;await api.list();assert.equal(state.open,true);
  });
  await test('one account is listed even though the client hides its row',async()=>{
    const {api}=environment(1);assert.equal((await api.list()).can_switch,false);
  });
  await test('submenu selection uses exact resource ID despite duplicate nicknames',async()=>{
    const {api,state}=environment(); const {proof}=await api.preflight({account_id:'103'});
    await api.switchOnce({account_id:'103',proof}); assert.equal(state.clicks.join(','),'103');
  });
  await test('draft and generation block switching before any click',async()=>{
    for(const code of ['DRAFT_PRESENT','GENERATING','ATTACHMENT_PRESENT']){
      const {api,state}=environment();state.busy=code;
      await assert.rejects(api.preflight({account_id:'102'}),e=>e.code===code);assert.equal(state.clicks.length,0);
    }
  });
  await test('route change between preflight and switch prevents clicking',async()=>{
    const {api,state}=environment(); const {proof}=await api.preflight({account_id:'102'});state.route='/chat/2';
    await assert.rejects(api.switchOnce({account_id:'102',proof}),e=>e.code==='ACCOUNT_CONTEXT_CHANGED');
    assert.equal(state.clicks.length,0);
  });
  await test('a draft appearing while submenu opens is preserved',async()=>{
    const {api,state}=environment();const {proof}=await api.preflight({account_id:'103'});
    state.onMore=()=>{state.busy='DRAFT_PRESENT';};
    await assert.rejects(api.switchOnce({account_id:'103',proof}),e=>e.code==='DRAFT_PRESENT');assert.equal(state.clicks.length,0);
  });
  await test('disabled account menu row is never clicked',async()=>{
    const {api,state}=environment();const {proof}=await api.preflight({account_id:'102'});state.disabled=true;
    await assert.rejects(api.switchOnce({account_id:'102',proof}),e=>e.code==='ACCOUNT_SWITCH_BUSY');assert.equal(state.clicks.length,0);
  });
  await test('an existing dialog blocks preflight',async()=>{
    const {api,state}=environment();state.dialogs=[{getClientRects:()=>[{}],textContent:'设置'}];
    await assert.rejects(api.preflight({account_id:'102'}),e=>e.code==='ACCOUNT_DIALOG_PRESENT');assert.equal(state.clicks.length,0);
  });
  await test('login prompt is reported without returning any dialog contents',async()=>{
    const {api,state}=environment();state.dialogs=[{getClientRects:()=>[{}],textContent:'扫码登录 private-dialog-data'}];
    const data=api.observe();assert.equal(data.login_required,true);assert.equal(JSON.stringify(data).includes('private-'),false);
  });
  await test('current identity is read from the committed fiber rather than a stale alternate',async()=>{
    const {api,avatar,root,avatarFiber}=environment();
    const staleRoot={stateNode:root.stateNode};
    avatar.__reactFiber$test={type:function Avatar(){},memoizedState:{memoizedState:{user_id_str:'999',name:'过期身份'}},
      return:staleRoot,alternate:avatarFiber};
    assert.equal(api.current().current_account.account_id,'101');
  });
  process.stdout.write(`${passed} account UI tests passed\n`);
}
main().catch(error=>{console.error(error);process.exitCode=1;});
