// Synthetic renderer regression checks; no real uploads or desktop actions.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/doubao_mcp/media.js'),'utf8');

function fixture() {
  const attachments=Array.from({length:8},(_,i)=>({type:'image',fileKey:`resource-${i+1}`,localKey:`local-${i+1}`,
    fileName:`frame-${i+1}.png`,status:'Normal',uploadPercent:100,size:100+i,md5:`hash-${i+1}`}));
  const state={doc:{type:'doc',content:[{type:'paragraph'}]},selection:null,focuses:0,foreignResource:false};
  const plain=node=>node.type==='text'?node.text:node.type==='inputEngineMention'?'@'+node.attrs.label:
    (node.content??[]).map(plain).join(node.type==='doc'?'\n':'');
  const text=()=>plain(state.doc);
  const paragraph=()=>state.doc.content[0];
  const setText=value=>{state.doc={type:'doc',content:[{type:'paragraph',content:value?[{type:'text',text:value}]:[]}]};};
  const rich={getJSON:()=>state.doc,getText:text,view:{dom:{get innerText(){return text();}}},
    state:{get selection(){return state.selection??{from:text().length+1,to:text().length+1};},
      doc:{textBetween:(from,to)=>text().slice(from-1,to-1)}},
    commands:{focus(){state.focuses++;state.selection=null;},setTextSelection(range){state.selection=range;},
      setContent(doc){state.doc=doc;}}};
  function type(value) {
    if(state.selection) {
      const {from,to}=state.selection;setText(text().slice(0,from-1)+value+text().slice(to-1));state.selection=null;
    } else {
      const nodes=paragraph().content??(paragraph().content=[]),last=nodes.at(-1);
      if(last?.type==='text')last.text+=value;else nodes.push({type:'text',text:value});
    }
  }
  const node=()=>({getClientRects:()=>[{}]});
  const area=node(),host=node();host.editor=rich;
  const root={stateNode:{}};root.stateNode.current=root;
  const stale=attachments.map(x=>({...x}));stale[7].status='Uploading';stale[7].uploadPercent=0;stale[7].fileKey=null;
  const oldParent={return:root,memoizedProps:{attachmentStates:stale}};
  const currentParent={return:root,memoizedProps:{attachmentStates:attachments}};
  const oldHost={return:oldParent},currentHost={return:oldParent}; // Both return chains reach the same root.
  oldHost.alternate=currentHost;currentHost.alternate=oldHost;
  oldParent.child=oldHost;currentParent.child=currentHost;root.child=currentParent;
  area.__reactFiber$test=oldHost;
  const container={getAttribute:()=> 'test-input'};
  const mention=asset=>({type:'inputEngineMention',attrs:{id:asset.fileKey,resourceId:asset.fileKey,
    label:asset.fileName,mentionType:'file',metadata:{resourceType:'image',locatorKind:'file-key'}}});
  function options() {
    const value=text(),at=value.lastIndexOf('@');
    if(at<0)return [];
    const query=value.slice(at+1);
    if(/\s/.test(query))return [];
    const matches=attachments.filter(a=>a.fileName.toLowerCase().includes(query.toLowerCase())).slice(0,5);
    return matches.map((asset,index)=>({textContent:asset.fileName,
      __reactFiber$test:{key:`option:current-attachment:${state.foreignResource?'wrong-resource':asset.fileKey}`},
      getAttribute:key=>key==='data-mention-option-index'?String(index):null,
      click(){const nodes=paragraph().content,last=nodes.at(-1);assert.equal(last.type,'text');
        const prefix=last.text.slice(0,last.text.lastIndexOf('@'));nodes.pop();
        if(prefix)nodes.push({type:'text',text:prefix});nodes.push(mention(asset));}
    }));
  }
  const document={
    querySelector(selector){
      if(selector.includes('attachment_area')||selector.includes('attachment-image-card'))return area;
      if(selector.includes('chat_input'))return container;
      return null;
    },
    querySelectorAll(selector){
      if(selector.includes('ProseMirror'))return [host];
      if(selector.includes('data-mention-option-index'))return options();
      return [];
    }
  };
  const context=vm.createContext({document,location:{pathname:'/chat'},doubao:{snapshot:()=>({generating:false})}});
  vm.runInContext(source+'\nglobalThis.api=doubaoMedia;',context);
  return {api:context.api,state,attachments,type,text,setText,mention,root};
}

let count=0;
function test(name,run){run();count++;process.stdout.write(`ok ${count} - ${name}\n`);}
test('bare trigger really contains only the first five options',()=>{
  const f=fixture();f.setText('@');assert.equal(f.api.candidates().candidates.length,5);
  assert.equal(f.api.candidates().candidates.some(x=>x.resource_id==='resource-8'),false);
});
test('eighth attachment is searched and selected as a real structured mention',()=>{
  const f=fixture(),start=f.api.beginBind({asset_id:'resource-8'});
  assert.equal(start.insert_text,'@frame-8.png');f.type(start.insert_text);
  assert.equal(f.api.candidates().candidates[0].resource_id,'resource-8');
  f.api.selectCandidate({asset_id:'resource-8',expected_context:start.context});
  const actual=f.api.inspect();assert.equal(actual.bindings[0].resource_id,'resource-8');
  assert.equal(actual.bindings[0].node.type,'inputEngineMention');
});
test('an unsuccessful bare trigger is completed rather than duplicated',()=>{
  const f=fixture();f.setText('@');const start=f.api.beginBind({asset_id:'resource-8'});
  assert.equal(start.insert_text,'frame-8.png');f.type(start.insert_text);assert.equal(f.text(),'@frame-8.png');
});
test('a pending filename query is resumed without inserting it again',()=>{
  const f=fixture();f.setText('@frame-8.png');const start=f.api.beginBind({asset_id:'resource-8'});
  assert.equal(start.trigger_already_present,true);assert.equal(start.insert_text,undefined);
});
test('space-containing filename uses a searchable token and the full real label',()=>{
  const f=fixture();f.attachments[7].fileName='reference 08 图片.png';
  const start=f.api.beginBind({asset_id:'resource-8'});assert.equal(/\s/.test(start.candidate_query),false);
  f.type(start.insert_text);f.api.selectCandidate({asset_id:'resource-8',expected_context:start.context});
  assert.equal(f.api.inspect().bindings[0].label,'reference 08 图片.png');
});
test('an old query broken by spaces replaces only its verified filename range',()=>{
  const f=fixture();f.attachments[7].fileName='reference 08 图片.png';f.setText('保留用户原文 @reference 08 图片.png');
  const start=f.api.beginBind({asset_id:'resource-8'});assert.equal(start.replaced_filename_query,true);
  f.type(start.insert_text);assert.equal(f.text().startsWith('保留用户原文 @'),true);
  f.api.selectCandidate({asset_id:'resource-8',expected_context:start.context});assert.equal(f.api.inspect().bindings.length,1);
});
test('adding a new mention preserves prior structured references and text',()=>{
  const f=fixture();f.state.doc.content[0].content=[{type:'text',text:'保留原文 '},f.mention(f.attachments[0])];
  const start=f.api.beginBind({asset_id:'resource-8'});f.type(start.insert_text);
  f.api.selectCandidate({asset_id:'resource-8',expected_context:start.context});
  assert.equal(f.api.inspect().bindings.length,2);assert.equal(f.text().startsWith('保留原文 @frame-1.png'),true);
});
test('same title with the wrong resource never becomes a binding',()=>{
  const f=fixture(),start=f.api.beginBind({asset_id:'resource-8'});f.type(start.insert_text);f.state.foreignResource=true;
  assert.throws(()=>f.api.selectCandidate({asset_id:'resource-8',expected_context:start.context}),e=>e.code==='IMAGE_CANDIDATE_UNAVAILABLE');
  assert.equal(f.api.inspect().bindings.length,0);
});
test('duplicate attachment labels fail before editing the draft',()=>{
  const f=fixture();f.attachments[6].fileName='frame-8.png';f.setText('user draft');
  assert.throws(()=>f.api.beginBind({asset_id:'resource-8'}),e=>e.code==='AMBIGUOUS_IMAGE_LABEL');assert.equal(f.text(),'user draft');
});
test('upload completion is read from committed children despite stale shared return links',()=>{
  const f=fixture();const actual=f.api.inspect();assert.equal(actual.assets[7].uploaded,true);assert.equal(actual.assets[7].resource_id,'resource-8');
});
test('a detached attachment branch is rejected instead of returning old upload status',()=>{
  const f=fixture();f.root.child=null;assert.throws(()=>f.api.inspect(),e=>e.code==='MEDIA_LAYOUT_CHANGED');
});
process.stdout.write(`${count} media UI tests passed\n`);
