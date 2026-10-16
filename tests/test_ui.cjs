const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/app.js', 'utf8');
const selection = source.slice(source.indexOf('function selectReading('), source.indexOf('async function queueModel('));
const readings = {
  old: {chunks:[{paragraph:0}], clips:[{words:[{start:0,start_time:0},{start:12,start_time:5}]}], chunk_offsets:[0]},
  next: {chunks:[{paragraph:0}], clips:[{duration:20,words:[{start:0,start_time:0},{start:12,start_time:9}]}], chunk_offsets:[0]},
};
const context = {
  state:{selected:'old',chunk:0,source:'clip',resumeTime:0}, audio:{currentTime:6},
  playbackChunks:()=>readings[context.state.selected].chunks,
  selectedReading:()=>readings[context.state.selected],
  document:{querySelector:()=>({closest:()=>({querySelectorAll:()=>[{dataset:{wordStart:'12'}}]})})},
  stopPlayback(){},localStorage:{getItem:()=>null},$:()=>({}),updateCards(){},updatePlayer(){},formatTime:String,toast(){},
};
vm.createContext(context); vm.runInContext(selection,context);
context.selectReading('next',true);
assert.equal(context.state.resumeTime,9,'Switch uses the new voice’s sentence timestamp, not the old clock');
assert.equal(context.state.chunk,0);
console.log('Same-sentence switch passed.');

const bulk = source.slice(source.indexOf("$('#render-combinations').onclick"), source.indexOf("$('#render-all').onclick"));
const buttons = {'#render-combinations':{},'#render-scope':{value:'audition'}};
const calls = [];
const bulkContext = {
  state:{article:{id:'original'},models:[{id:'ready',installed:true,downloaded:true,voices:[{id:'one'},{id:'two'}]},{id:'missing',installed:false,voices:[{id:'skip'}]}]},
  $:s=>buttons[s], renderSavedVoices(){}, refreshArticle:async()=>{},refreshQueue:async()=>{},toast(){},
  api:async(url,body)=>{calls.push({url,body});bulkContext.state.article.id='changed';},
};
vm.createContext(bulkContext);vm.runInContext(bulk,bulkContext);
(async()=>{
  await buttons['#render-combinations'].onclick();
  assert.equal(calls.length,2);assert(calls.every(c=>c.url.includes('/original/')&&c.body.audition));
  assert.equal(bulkContext.state.bulkRendering,false);
  buttons['#render-scope'].value='full';calls.length=0;
  await buttons['#render-combinations'].onclick();assert(calls.every(c=>c.body.audition===false));
  console.log('Bulk audition/full scope and stable article selection passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
