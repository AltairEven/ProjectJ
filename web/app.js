"use strict";
const $ = (id) => document.getElementById(id);
let state = null, config = null, course = null, prep = null, report = null;
let busy = false, epoch = 0, audio = null, audioURL = null, recorder = null, stream = null;
let pending = new Set();
const phases = {opening:"开场与目标",baseline:"阅读与事实",listening:"听力与动作",oral:"英语表达",practice:"按需帮助",offscreen:"离屏应用",closing:"小结与建议"};
const levels = {unknown:"帮助未知",independent:"报告无内容帮助",cue:"轻提示",scaffold:"支架支持",model:"完整示范"};
function showError(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, data, raw) {
  const controller = new AbortController(); pending.add(controller);
  try {
    const response = await fetch(path, {method:data === undefined ? "GET":"POST",
      headers:data === undefined ? {}:{"Content-Type":raw ? raw.type : "application/json"},
      body:data === undefined ? undefined:raw || JSON.stringify(data), signal:controller.signal});
    if (!response.ok) { const value = await response.json(); throw new Error(value.error || "请求未完成"); }
    return response.headers.get("content-type").startsWith("audio/") ? response.blob():response.json();
  } finally { pending.delete(controller); }
}
function switchTab(tab) {
  ["classroom","preparation","review"].forEach((name) => { $("tab-"+name).hidden = name !== tab; });
  document.querySelectorAll(".nav").forEach((b) => b.classList.toggle("active",b.dataset.tab===tab));
  $("breadcrumb").textContent = "学习空间 / " + {classroom:"课堂",preparation:"备课",review:"课后反馈"}[tab];
  if (tab === "review") renderReview();
}
function silence(cancelRequests=false) {
  epoch++; if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  if (audio) { audio.pause(); audio = null; }
  if (audioURL) { URL.revokeObjectURL(audioURL); audioURL = null; }
  if (recorder && recorder.state === "recording") recorder.stop();
  if (stream) { stream.getTracks().forEach((track)=>track.stop()); stream=null; }
  if(cancelRequests) pending.forEach((controller)=>controller.abort());
  $("audio-status").textContent = ""; $("mic").textContent = "录音转写";
}
function setBusy(value) {
  busy = value;
  ["send","ack","action-send","offscreen-send","mic","speak"].forEach((id) => { $(id).disabled=value; });
  if (state) {
    const ended = ["completed","stopped"].includes(state.status);
    document.querySelectorAll(".controls button").forEach((b)=>{ b.disabled=value || ended; });
    $("stop").disabled = ended;
    $("mic").disabled=value || !config.model_configured || !window.MediaRecorder || ended || state.task_kind==="offscreen";
  }
}
function render() {
  if (!state) return;
  $("welcome").hidden=true; $("classroom").hidden=false;
  $("phase").textContent=phases[state.phase] || "本次任务";
  $("elapsed").textContent=Math.floor(state.elapsed_seconds/60)+":"+String(state.elapsed_seconds%60).padStart(2,"0");
  $("utterance").textContent=state.utterance;
  $("sample-count").textContent=state.sample_count;
  $("help-info").textContent=levels[state.help_level]+"；不把帮助后回答当作独立完成。";
  const ended=["completed","stopped"].includes(state.status), paused=state.status==="paused";
  $("answer-area").hidden=ended || paused || ["ack","summary","action","offscreen"].includes(state.task_kind);
  $("ack-area").hidden=ended || paused || !["ack","summary"].includes(state.task_kind);
  $("ack").textContent=state.task_kind==="summary" ? "确认小结，结束本次检查":"确认，继续 →";
  $("action-area").hidden=ended || paused || state.task_kind!=="action";
  $("offscreen-area").hidden=ended || paused || state.task_kind!=="offscreen";
  $("finished").hidden=!ended;
  $("pause").dataset.event=paused?"resume":"pause"; $("pause").textContent=paused?"恢复":"暂停";
  $("sequence-cards").replaceChildren();
  (state.cards || []).forEach((card)=>{ const el=document.createElement("div"), b=document.createElement("b"); b.textContent=card.id; el.append(b,document.createTextNode(card.text)); $("sequence-cards").append(el); });
  $("material").hidden=!state.material || ended;
  if(state.material) { $("material-title").textContent=state.material.title; $("material-text").textContent=state.material.text; $("material-grade").textContent=state.material.grading; }
  $("flow").replaceChildren();
  if(prep) prep.tasks.forEach((task)=>{
    const el=document.createElement("li"); el.textContent=task.id+" · "+(phases[task.phase] || task.phase);
    el.classList.toggle("current",task.id===state.task_id); $("flow").append(el);
  });
  if(state.task_kind==="offscreen" && !$("offscreen-fields").children.length) {
    state.offscreen_questions.forEach((q)=>{ const label=document.createElement("label"),input=document.createElement("input"); label.textContent=q.prompt; label.htmlFor="off-"+q.id; input.id="off-"+q.id; input.placeholder="实际原回答；不要替学生补答案"; $("offscreen-fields").append(label,input); });
  }
  setBusy(busy); renderReview();
}
async function submit(event) {
  if(!state) return;
  showError(""); silence(); setBusy(true);
  const payload={session_id:state.session_id,revision:state.revision,event:{source:"parent_report",...event}};
  try { state=await api("/api/event",payload); render(); $("answer").value=""; if($("voice").value!=="off" && state.status!=="stopped") await speak(); }
  catch(e) { if(e.name!=="AbortError") showError(e.message); }
  finally { setBusy(false); }
}
async function speak() {
  if(!state || state.status==="stopped" || $("voice").value==="off") return;
  silence(); const token=epoch, view=state;
  $("audio-status").textContent="准备朗读…";
  try {
    if($("voice").value==="browser") {
      if(!("speechSynthesis" in window)) throw new Error("这台浏览器没有电脑朗读，请改用文字。");
      const u=new SpeechSynthesisUtterance(view.utterance); u.lang="zh-CN"; u.rate=.9;
      const local=window.speechSynthesis.getVoices().find(v=>v.localService && v.lang.startsWith("zh"));
      if(local) u.voice=local;
      u.onend=()=>{ if(token===epoch) $("audio-status").textContent="电脑报告播放结束；听清仍由你确认。"; };
      u.onerror=()=>{ if(token===epoch) $("audio-status").textContent="朗读未完成，可以直接读文字。"; };
      window.speechSynthesis.speak(u);
    } else {
      const blob=await api("/api/speech",{session_id:view.session_id,revision:view.revision});
      if(token!==epoch || !state || state.revision!==view.revision) return;
      audioURL=URL.createObjectURL(blob); audio=new Audio(audioURL);
      audio.onended=()=>{ if(token===epoch) $("audio-status").textContent="播放结束；听清仍由你确认。"; };
      await audio.play();
    }
  } catch(e) { if(e.name!=="AbortError" && token===epoch) showError(e.message); }
}
async function recordTurn() {
  if(recorder && recorder.state==="recording") { recorder.stop(); $("mic").textContent="正在转写…"; return; }
  if(!state || !config.model_configured) return;
  silence(); showError(""); const view=state, token=epoch;
  try {
    const turnStream=await navigator.mediaDevices.getUserMedia({audio:true});
    if(token!==epoch || !state || state.revision!==view.revision || state.status!=="active") {
      turnStream.getTracks().forEach(t=>t.stop()); return;
    }
    stream=turnStream;
    const mime=["audio/webm","audio/mp4","audio/ogg"].find(m=>MediaRecorder.isTypeSupported(m));
    if(!mime) throw new Error("浏览器没有受支持的录音格式，请用文字。");
    const turnRecorder=new MediaRecorder(turnStream,{mimeType:mime}); recorder=turnRecorder; const chunks=[];
    turnRecorder.ondataavailable=(e)=>{ if(e.data.size) chunks.push(e.data); };
    turnRecorder.onstop=async()=>{
      turnStream.getTracks().forEach(t=>t.stop()); if(stream===turnStream) stream=null;
      if(token!==epoch) return;
      $("mic").textContent="录音转写";
      setBusy(true);
      try {
        const blob=new Blob(chunks,{type:mime});
        const result=await api("/api/transcribe?id="+view.session_id+"&revision="+view.revision,{},blob);
        if(token===epoch && state && result.revision===state.revision) { $("answer").value=result.text; $("mic-status").textContent="请核对转写，再发送实际回答。"; }
      } catch(e) { if(e.name!=="AbortError" && token===epoch) showError(e.message); } finally { if(token===epoch) setBusy(false); }
    };
    turnRecorder.start(); $("mic").textContent="结束录音";
  } catch(e) { silence(); showError("录音未开始："+e.message); }
}
function renderReview() {
  const ended=state && ["completed","stopped"].includes(state.status);
  $("review-wait").hidden=ended; $("review-form").hidden=!ended || !!report;
  if(report) {
    $("review-result").hidden=false;
    let text="已保存成人检查反馈。本次收到 "+report.samples.length+" 个回答样本，结束方式："+(report.status==="completed"?"正常收尾":"明确停止")+"。\n\n家长原话："+report.parent_feedback;
    if(report.ai_review) text+="\n\nAI 整理："+(report.ai_review.summary || report.ai_review.reason)+"\n"+(report.ai_review.findings || []).join("\n");
    $("review-result").textContent=text; $("export").hidden=false;
  }
}
async function init() {
  try {
    [config,course,prep]=await Promise.all([api("/api/config"),api("/api/course"),api("/api/preparation")]);
    if(config.model_configured) {
      const option=$("provider").options[1]; option.disabled=false; option.textContent="OpenAI · Sol / Low";
      $("voice").options[2].disabled=false; $("voice").options[2].textContent="OpenAI 语音";
      $("prep-ai").disabled=false; $("review-ai").disabled=false;
    }
    prep.materials.forEach((m)=>{const row=document.createElement("div"); row.className="prep-item"; row.textContent=m.id+" · "+m.title+" — "+m.grading; $("prep-summary").append(row);});
    const row=document.createElement("div"); row.className="prep-item"; row.textContent="结构检查："+(prep.errors.length?"有缺口":"通过")+"；正式课堂："+prep.readiness.reason; $("prep-summary").append(row);
    prep.tasks.forEach((task)=>{ const el=document.createElement("li"); el.textContent=task.id+" · "+(phases[task.phase] || task.phase); $("flow").append(el); });
  } catch(e) { showError(e.message); $("start").disabled=true; }
}
document.querySelectorAll(".nav").forEach(b=>b.addEventListener("click",()=>switchTab(b.dataset.tab)));
$("start").addEventListener("click",async()=>{
  if(state && !["completed","stopped"].includes(state.status)) return;
  showError(""); $("start").disabled=true;
  try { state=await api("/api/sessions",{provider:$("provider").value,mode:"adult"}); render(); }
  catch(e) { showError(e.message); } finally { $("start").disabled=false; }
});
$("send").addEventListener("click",()=>submit({kind:"auto",text:$("answer").value}));
$("answer").addEventListener("keydown",(e)=>{if(e.key==="Enter" && !e.shiftKey && !busy){e.preventDefault(); $("send").click();}});
$("ack").addEventListener("click",()=>submit({kind:"ack",confirmed:true,delivery:$("delivery").value}));
$("action-send").addEventListener("click",()=>submit({kind:"action_report",done:$("action-done").checked,text:$("action-text").value}));
$("offscreen-send").addEventListener("click",()=>{const answers={}; state.offscreen_questions.forEach(q=>{answers[q.id]=$("off-"+q.id).value;}); submit({kind:"offscreen_report",done:$("offscreen-done").checked,answers,help_level:$("offscreen-help").value,experiment:$("experiment").value || null});});
document.querySelectorAll("[data-event]").forEach(b=>b.addEventListener("click",()=>submit({kind:b.dataset.event})));
$("stop").addEventListener("click",async()=>{silence(true); busy=false; await submit({kind:"stop",explicit:true});});
$("speak").addEventListener("click",speak); $("voice").addEventListener("change",()=>silence()); $("mic").addEventListener("click",recordTurn);
$("print").addEventListener("click",()=>window.print()); $("go-review").addEventListener("click",()=>{silence();switchTab("review");});
$("prep-ai").addEventListener("click",async()=>{
  $("prep-ai").disabled=true; showError("");
  try {const value=await api("/api/preparation/review",{}); $("prep-ai-result").hidden=false; $("prep-ai-result").textContent=value.summary+"\n\n"+value.findings.join("\n");}
  catch(e){showError(e.message);}finally{$("prep-ai").disabled=!config.model_configured;}
});
$("save-feedback").addEventListener("click",async()=>{
  silence(); $("save-feedback").disabled=true; showError("");
  try {report=await api("/api/feedback",{session_id:state.session_id,feedback:$("feedback").value,ai:$("review-ai").checked});renderReview();}
  catch(e){showError(e.message);}finally{$("save-feedback").disabled=false;}
});
$("export").addEventListener("click",()=>{if(!report)return;const blob=new Blob([JSON.stringify(report,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="project-j-adult-"+report.session_id+".json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
window.addEventListener("pagehide",()=>silence(true));
init();
