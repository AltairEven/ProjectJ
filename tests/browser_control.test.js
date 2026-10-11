// Simulated browser APIs only. These checks do not request a microphone or play sound.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function deferred(){let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
async function fixture(){
  const elements=new Map();
  function element(id){
    if(!elements.has(id)) elements.set(id,{value:id==='voice'?'openai':'',hidden:false,disabled:false,
      textContent:'',dataset:{},children:[],options:[{},{},{}],addEventListener(){},
      append(...items){this.children.push(...items);},replaceChildren(){this.children=[];}});
    return elements.get(id);
  }
  const stats={stops:0,recorders:0,plays:0};
  const media=deferred(),speech=deferred();
  const tracks=[{stop(){stats.stops++;}}];
  const turnStream={getTracks:()=>tracks};
  class Recorder {
    static isTypeSupported(){return true;}
    constructor(){stats.recorders++;this.state='inactive';}
    start(){this.state='recording';}
    stop(){this.state='inactive';queueMicrotask(()=>this.onstop?.());}
  }
  const context=vm.createContext({
    document:{getElementById:element,querySelectorAll:()=>[],createElement:element,
      createTextNode:text=>text},
    window:{MediaRecorder:Recorder,addEventListener(){}}, MediaRecorder:Recorder,
    navigator:{mediaDevices:{getUserMedia:()=>media.promise}},
    Audio:class {constructor(){stats.plays++;}play(){return Promise.resolve();}pause(){}},
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
    AbortController,Blob,Set,console,setTimeout,queueMicrotask,
    fetch:async path=> {
      if(path==='/api/speech') return speech.promise;
      const data=path==='/api/config'?{model_configured:true}:path==='/api/preparation'?
        {tasks:[],materials:[],errors:[],readiness:{reason:'technical fixture'}}:{};
      return {ok:true,headers:{get:()=> 'application/json'},json:async()=>data};
    }
  });
  vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
  await new Promise(r=>setImmediate(r));
  vm.runInContext('state={session_id:"test",revision:1,status:"active",task_kind:"answer",utterance:"test"}',context);
  return {context,stats,media,speech,turnStream};
}

test('permission resolves after stop: release tracks, never start recorder',async()=>{
  const f=await fixture();
  const pending=vm.runInContext('recordTurn()',f.context);
  vm.runInContext('silence(true);state.status="stopped"',f.context);
  f.media.resolve(f.turnStream);await pending;
  assert.equal(f.stats.stops,1);assert.equal(f.stats.recorders,0);
});

test('cancel active recording: do not transcribe or advance',async()=>{
  const f=await fixture();f.media.resolve(f.turnStream);
  await vm.runInContext('recordTurn()',f.context);
  assert.equal(f.stats.recorders,1);
  vm.runInContext('silence(true);state.status="stopped"',f.context);
  await new Promise(r=>setImmediate(r));
  assert.equal(vm.runInContext('pending.size',f.context),0);
  assert.equal(vm.runInContext('state.revision',f.context),1);
  assert.equal(f.stats.plays,0);
});

test('late synthesized response after stop: no audio object or playback',async()=>{
  const f=await fixture();
  const pending=vm.runInContext('speak()',f.context);
  vm.runInContext('silence(true);state.status="stopped"',f.context);
  f.speech.resolve({ok:true,headers:{get:()=> 'audio/mpeg'},blob:async()=>new Blob(['test'])});
  await pending;assert.equal(f.stats.plays,0);
});
