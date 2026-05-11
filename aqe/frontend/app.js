/* AQE Platform — app.js  (split-panel run-tests UX) */
(function () {
"use strict";

const API = "/api/v1";
const WS_BASE = `ws://${location.host}/api/v1`;

// ── helpers ──────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const show = el => { if(el) el.style.display=""; };
const hide = el => { if(el) el.style.display="none"; };
const setText = (id,v) => { const e=$(id); if(e) e.textContent=v??'—'; };
const fmtDate = iso => iso ? new Date(iso).toLocaleString("en-US",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "—";
const fmtDur  = s  => s<60 ? `${Number(s).toFixed(1)}s` : `${(s/60).toFixed(1)}m`;
const sleep   = ms => new Promise(r=>setTimeout(r,ms));

async function apiFetch(path, opts={}) {
  const res = await fetch(API+path,{headers:{"Content-Type":"application/json"},...opts});
  let body=null; try{body=await res.json();}catch(_){}
  if(!res.ok){const e=new Error((body&&body.detail)||`HTTP ${res.status}`);e.status=res.status;throw e;}
  return body;
}

function pillHtml(s,sm){
  const m={COMPLETED:"p-green",ok:"p-green",PASSED:"p-green",FAILED:"p-red",CANCELLED:"p-red",
           ERROR:"p-amber",WAITING_FOR_INPUT:"p-amber",degraded:"p-amber",
           EXECUTING:"p-blue",PLANNING:"p-blue",AWAITING_APPROVAL:"p-blue",IDLE:"p-slate"};
  return `<span class="pill ${m[s]||"p-slate"}">${s}</span>`;
}

// ── View routing ─────────────────────────────────────────────────
const VIEWS=["dashboard","changes","run-tests","reports","graphrag","settings"];
let _activeView=null;

function navigateTo(view){
  if(_activeView===view) return;
  _activeView=view;
  VIEWS.forEach(v=>{
    const sec=$("view-"+v), btn=document.querySelector(`[data-view="${v}"]`);
    if(sec) sec.className="view"+(v===view?" active":"");
    if(btn) btn.classList.toggle("active",v===view);
  });
  if(view==="dashboard") loadDashboard();
  if(view==="changes")   loadChanges(false);
  if(view==="run-tests") loadIdleSessions();
  if(view==="reports")   loadReports();
  if(view==="graphrag")  {loadGraph();loadQStatus();}
  if(view==="settings")  loadSettings();
}
window.navigateTo=navigateTo;
$("main-nav").addEventListener("click",e=>{
  const b=e.target.closest("[data-view]"); if(b) navigateTo(b.dataset.view);
});

// ── Status dots ───────────────────────────────────────────────────
async function pollStatus(){
  try{
    const h=await fetch("/health").then(r=>r.json());
    const c=h.checks||{};
    function s(dotId,lblId,v){
      const d=$(dotId),l=$(lblId); if(!d||!l) return;
      d.className="dot "+(v==="ok"?"dot-green":v==="unreachable"?"dot-red":"dot-amber");
      l.textContent=v;
    }
    s("dot-aqe","lbl-aqe",h.status);
    s("dot-target","lbl-target",c.target_api||"?");
  }catch(_){
    const d=$("dot-aqe"); if(d) d.className="dot dot-red";
  }
}

// ══════════════════════════════════════════════════════════════════
// DASHBOARD
// ══════════════════════════════════════════════════════════════════
window.loadDashboard = async function(){
  // Health
  try{
    const h=await fetch("/health").then(r=>r.json());
    const c=h.checks||{};
    function hlt(id,v){
      const el=$(id); if(!el) return;
      const dot=el.querySelector(".dot");
      if(dot) dot.className="dot "+(v==="ok"?"dot-green":v==="unreachable"?"dot-red":"dot-amber");
      el.innerHTML=`<span class="dot ${v==="ok"?"dot-green":v==="unreachable"?"dot-red":"dot-amber"}"></span>
        <span style="font-size:13px;">${id.replace("hlt-","").replace("aqe","AQE API").replace("target","Target :8000").replace("qdrant","Qdrant :6333").replace("neo4j","Neo4j :7687")}</span>
        <span style="font-size:12px;font-weight:600;color:${v==="ok"?"#10b981":v==="unreachable"?"#ef4444":"#f59e0b"};margin-left:4px;">${v}</span>`;
    }
    hlt("hlt-aqe",h.status); hlt("hlt-target",c.target_api||"?");
    hlt("hlt-qdrant",c.qdrant||"?"); hlt("hlt-neo4j",c.neo4j||"?");
    setText("hlt-ts","Last checked "+new Date().toLocaleTimeString());
  }catch(_){}

  // Sessions
  try{
    const d=await apiFetch("/sessions");
    const ss=d.sessions||[];
    setText("k-total",d.total);
    const comp=ss.filter(s=>s.state==="COMPLETED");
    const active=ss.filter(s=>["PLANNING","EXECUTING","AWAITING_APPROVAL","WAITING_FOR_INPUT"].includes(s.state));
    setText("k-completed",comp.length);
    setText("k-active",active.length);
    setText("active-badge",active.length);
    const totalTests=ss.reduce((a,s)=>(s.results?.length||0)+a,0);
    setText("k-tests",totalTests||"—");
    setText("k-total-sub",`${comp.length} completed, ${active.length} active`);

    const al=$("active-list");
    al.innerHTML=active.length?active.map(s=>`
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid #f1f5f9;border-radius:8px;background:#fff;">
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${s.name||s.id.slice(0,10)}</div>
          <div class="mono" style="font-size:10px;color:#94a3b8;">${s.id.slice(0,18)}…</div>
        </div>
        ${pillHtml(s.state)}
        <button class="btn btn-ghost btn-sm" onclick="openLive('${s.id}')">Live →</button>
      </div>`).join(""):
      `<div style="color:#94a3b8;font-size:13px;text-align:center;padding:20px 12px;">No active sessions</div>`;
  }catch(_){}

  // Reports
  try{
    const r=await apiFetch("/reports");
    const reps=(r.reports||[]).slice(0,8);
    const tb=$("dash-reports"),em=$("dash-reports-empty");
    if(!reps.length){if(tb)tb.innerHTML="";if(em)em.style.display="";return;}
    if(em)em.style.display="none";
    tb.innerHTML=reps.map(rp=>{
      const pct=rp.total?Math.round(rp.passed/rp.total*100):0;
      const col=pct>=80?"#10b981":pct>=50?"#f59e0b":"#ef4444";
      return `<tr onclick="openReport('${rp.id}','${rp.session_name||rp.session_id.slice(0,8)}')">
        <td style="font-weight:600;">${rp.session_name||rp.session_id.slice(0,8)}</td>
        <td>
          <div style="display:flex;align-items:center;gap:6px;">
            <div class="prog-track" style="width:70px;"><div class="prog-fill" style="width:${pct}%;background:${col};"></div></div>
            <span style="font-weight:700;color:${col};font-size:12px;">${pct}%</span>
          </div>
        </td>
        <td><span style="color:#10b981;font-weight:600;">${rp.passed}</span></td>
        <td><span style="color:#ef4444;font-weight:600;">${rp.failed}</span></td>
        <td style="font-size:12px;color:#64748b;">${fmtDur(rp.duration_seconds)}</td>
        <td style="font-size:11px;color:#94a3b8;">${fmtDate(rp.created_at)}</td>
        <td><span style="color:#10b981;font-size:11px;font-weight:600;">View →</span></td>
      </tr>`;
    }).join("");
  }catch(_){}

  // Qdrant
  try{
    const g=await apiFetch("/graphrag/status");
    setText("k-vectors",g.qdrant?.points_count??0);
  }catch(_){}

  // Change-detection banner
  loadChangesStatus();
};

// ══════════════════════════════════════════════════════════════════
// CHANGE DETECTION
// ══════════════════════════════════════════════════════════════════
async function loadChangesStatus(){
  try{
    const s=await apiFetch("/changes/status");
    const banner=$("dash-changes-banner");
    const badge=$("nav-changes-badge");
    if(s.is_empty){
      if(banner) banner.style.display="none";
      if(badge){badge.style.display="none"; badge.textContent="0";}
      return;
    }
    if(banner) banner.style.display="";
    if(badge){badge.style.display=""; badge.textContent=s.file_count;}
    setText("dash-changes-title",
      `${s.file_count} file${s.file_count===1?"":"s"} changed since baseline`);
    setText("dash-changes-subtitle",
      `+${s.total_additions}/-${s.total_deletions} lines on branch ${s.branch} (HEAD ${s.head_sha.slice(0,7)})`);
  }catch(_){
    // change-detection requires git repo — silently hide banner if unavailable
    const banner=$("dash-changes-banner");
    if(banner) banner.style.display="none";
  }
}

const _RISK_PILL={low:"p-slate",medium:"p-amber",high:"p-red",critical:"p-red"};

async function loadChanges(forceRefresh){
  const empty=$("changes-empty"), analysis=$("changes-analysis"),
        suggested=$("changes-suggested-wrap"), filesWrap=$("changes-files-wrap"),
        loading=$("changes-loading");
  [empty, analysis, suggested, filesWrap].forEach(el=>{if(el) el.style.display="none";});
  if(loading) loading.style.display="";

  let ctx;
  try{
    ctx = forceRefresh
      ? await apiFetch("/changes/refresh",{method:"POST"})
      : await apiFetch("/changes/since-baseline");
  }catch(e){
    if(loading) loading.style.display="none";
    if(empty){
      empty.style.display="";
      empty.innerHTML=`<div style="color:#dc2626;font-weight:700;font-size:14px;">Change detection unavailable</div>
        <div style="font-size:12px;color:#64748b;margin-top:6px;">${e.message||"unknown error"}</div>
        <div style="font-size:11px;color:#94a3b8;margin-top:8px;">Did you run <span class="mono">demo_scripts\\setup_demo_repo.ps1</span>?</div>`;
    }
    return;
  }
  if(loading) loading.style.display="none";

  const cs=ctx.change_set, a=ctx.analysis;
  if(cs.is_empty || cs.file_count===0){
    if(empty){
      empty.style.display="";
      setText("changes-empty-sha", (cs.baseline_sha||"").slice(0,12));
    }
    return;
  }

  // Analysis card
  if(analysis){
    analysis.style.display="";
    const riskPill=$("changes-risk");
    if(riskPill){
      riskPill.className="pill "+(_RISK_PILL[a.risk_level]||"p-slate");
      riskPill.textContent=(a.risk_level||"low").toUpperCase()+" RISK";
    }
    setText("changes-summary", a.summary || "(no summary)");
    const mods=$("changes-modules");
    if(mods){
      mods.innerHTML=(a.modules_affected||[]).map(m=>
        `<span class="pill p-blue" style="font-size:11px;">${m}</span>`).join("")
        || `<span style="font-size:12px;color:#94a3b8;">No specific modules identified</span>`;
    }
    const issuesWrap=$("changes-issues-wrap"), issuesUl=$("changes-issues");
    if(a.detected_issues && a.detected_issues.length){
      if(issuesWrap) issuesWrap.style.display="";
      if(issuesUl) issuesUl.innerHTML=a.detected_issues.map(i=>`<li>${escapeHtml(i)}</li>`).join("");
    }else if(issuesWrap){issuesWrap.style.display="none";}
    const link=$("changes-commit-link");
    if(link){
      if(a.github_commit_url){link.href=a.github_commit_url; link.style.display="";}
      else{link.style.display="none";}
    }
  }

  // Suggested tests
  if((a.suggested_new_tests||[]).length){
    if(suggested) suggested.style.display="";
    const list=$("changes-suggested");
    if(list){
      list.innerHTML=a.suggested_new_tests.map((t,i)=>{
        const catCls={Vulnerability:"p-red",Security:"p-red",Performance:"p-violet",Header:"p-amber",
                       UI:"p-blue",Unit:"p-slate",API:"p-blue",Integration:"p-blue",Functional:"p-blue"};
        return `<div style="display:flex;align-items:flex-start;gap:10px;padding:9px 12px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;">
          <div style="width:20px;height:20px;border-radius:50%;background:#f1f5f9;color:#64748b;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;">${i+1}</div>
          <div style="flex:1;min-width:0;">
            <div style="font-size:12.5px;font-weight:600;color:#0f172a;">${escapeHtml(t.name)}</div>
            <div style="font-size:11px;color:#475569;margin-top:2px;">${escapeHtml(t.description||"")}</div>
            <div style="font-size:10.5px;color:#94a3b8;margin-top:3px;font-style:italic;">${escapeHtml(t.rationale||"")}</div>
          </div>
          <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;flex-shrink:0;">
            <span class="pill ${catCls[t.category]||"p-slate"}" style="font-size:10px;">${t.category}</span>
            <span style="font-size:10px;color:#94a3b8;">${t.module}</span>
          </div>
        </div>`;
      }).join("");
    }
  }

  // Files
  if(filesWrap){
    filesWrap.style.display="";
    setText("changes-files-count", cs.file_count);
    setText("changes-sha-summary",
      `${cs.baseline_sha.slice(0,7)} → ${cs.head_sha.slice(0,7)}  (+${cs.total_additions}/-${cs.total_deletions})`);
    const tb=$("changes-files-tbody");
    const statusCls={added:"p-green",modified:"p-blue",deleted:"p-red",renamed:"p-violet"};
    if(tb){
      tb.innerHTML=(cs.files||[]).map(f=>`
        <tr>
          <td><span class="pill ${statusCls[f.status]||"p-slate"}" style="font-size:10px;">${f.status}</span></td>
          <td class="mono" style="font-size:11.5px;">${escapeHtml(f.path)}</td>
          <td style="font-size:11px;color:#64748b;">${f.language}</td>
          <td style="text-align:right;font-family:'JetBrains Mono',monospace;font-size:11px;">
            <span style="color:#10b981;">+${f.additions}</span>
            <span style="color:#94a3b8;margin:0 4px;">/</span>
            <span style="color:#ef4444;">-${f.deletions}</span>
          </td>
        </tr>`).join("");
    }
  }
}
window.loadChanges=loadChanges;

async function startChangeDrivenSession(planMode){
  const name=`Change-driven (${planMode}) ${new Date().toLocaleTimeString()}`;
  // All modules by default — runners auto-skip irrelevant categories
  const modules=["Customers","Accounts","CreditCards","Deposits","Transactions","UI"];
  try{
    const sess=await apiFetch("/sessions",{method:"POST",body:JSON.stringify({
      name, modules, test_types:["All"], plan_mode:planMode, use_change_context:true,
    })});
    navigateTo("run-tests");
    _sid=sess.id;
    _lPass=0;_lFail=0;_lErr=0;_lTotal=0;
    resetExecUI();
    showRp("rp-planning");
    addPlanLog(`Session created (mode=${planMode}). Claude is generating the plan…`,"info");
    connectWs(sess.id, true);
    // Poll for plan
    for(let i=0;i<90;i++){
      await sleep(2000);
      const s=await apiFetch(`/sessions/${sess.id}`).catch(()=>null);
      if(!s) continue;
      if(s.state==="AWAITING_APPROVAL"){renderPlan(s.plan); return;}
      if(["FAILED","CANCELLED"].includes(s.state)){
        addPlanLog(`Session ${s.state}: ${s.error_message||""}`, "fail");
        return;
      }
    }
  }catch(e){
    alert("Failed to start change-driven session: "+e.message);
  }
}
window.startChangeDrivenSession=startChangeDrivenSession;

function escapeHtml(s){
  if(s==null) return "";
  return String(s).replace(/[&<>"']/g, ch=>(
    {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]
  ));
}

function openReport(id,title){
  navigateTo("reports");
  setTimeout(()=>openReportInline(id,title),150);
}

// ══════════════════════════════════════════════════════════════════
// RUN TESTS — split panel
// ══════════════════════════════════════════════════════════════════
let _sid=null, _ws=null, _startT=null, _timerIv=null;
let _lPass=0, _lFail=0, _lErr=0, _lTotal=0;
let _doneReportId=null, _pendingFiles=[];

// Module toggle
window.toggleMod=function(el){el.classList.toggle("sel");};
function getSelectedMods(){return[...document.querySelectorAll(".mod-card.sel")].map(e=>e.dataset.mod);}

// Script upload
const dz=$("drop-z"), fu=$("file-up");
if(dz&&fu){
  ["dragenter","dragover"].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault();dz.classList.add("over");}));
  ["dragleave","drop"].forEach(e=>dz.addEventListener(e,ev=>{ev.preventDefault();dz.classList.remove("over");}));
  dz.addEventListener("drop",e=>addFiles(e.dataTransfer.files));
  fu.addEventListener("change",e=>addFiles(e.target.files));
}
function addFiles(files){
  const list=$("scripts-list");
  [...files].forEach(f=>{
    _pendingFiles.push(f);
    const d=document.createElement("div");
    d.style.cssText="display:flex;align-items:center;gap:6px;padding:5px 9px;border:1px solid #e2e8f0;border-radius:6px;font-size:11px;";
    d.innerHTML=`<span style="color:#10b981;">✓</span><span style="font-weight:500;">${f.name}</span><span style="color:#94a3b8;">${(f.size/1024).toFixed(0)}KB</span>`;
    if(list)list.appendChild(d);
  });
}

// Right-panel state switcher
function showRp(id){
  ["rp-idle","rp-planning","rp-plan","rp-exec","rp-done"].forEach(s=>{
    const el=$(s); if(el) el.className="rp-state"+(s===id?" rp-active":"");
  });
}

// Load recent sessions for idle panel
window.loadIdleSessions=async function(){
  showRp("rp-idle");
  const il=$("idle-sessions"); if(!il) return;
  try{
    const d=await apiFetch("/sessions");
    const ss=(d.sessions||[]).slice(0,5);
    il.innerHTML=ss.length?ss.map(s=>`
      <div style="display:flex;align-items:center;gap:8px;padding:9px 12px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;cursor:pointer;" onclick="openLive('${s.id}')">
        <div style="flex:1;min-width:0;text-align:left;">
          <div style="font-size:13px;font-weight:600;">${s.name||s.id.slice(0,12)}</div>
          <div class="mono" style="font-size:10px;color:#94a3b8;">${fmtDate(s.created_at)}</div>
        </div>
        ${pillHtml(s.state)}
        <span style="color:#94a3b8;font-size:11px;">→</span>
      </div>`).join(""):
      `<div style="font-size:12px;color:#94a3b8;text-align:center;padding:12px;">No previous sessions</div>`;
  }catch(_){}
};

// Open a previous session in the live panel
function openLive(sid){
  navigateTo("run-tests");
  _sid=sid;
  _lPass=0;_lFail=0;_lErr=0;_lTotal=0;
  resetExecUI();
  showRp("rp-exec");
  connectWs(sid);
  // Load existing state
  apiFetch(`/sessions/${sid}`).then(s=>{
    updateExecState(s.state);
    (s.results||[]).forEach(r=>{
      _lTotal++;
      if(r.status==="PASSED")_lPass++;
      else if(r.status==="FAILED")_lFail++;
      else _lErr++;
    });
    updateExecProg();
    if(s.clarification_question) showClarif(s.clarification_question);
    if(s.report_id){_doneReportId=s.report_id;}
    if(["COMPLETED","FAILED","CANCELLED"].includes(s.state)){
      showDone(s);
    } else if(s.started_at){
      _startT=new Date(s.started_at);startTimer();
    }
  }).catch(()=>{});
}
window.openLive=openLive;

// Generate plan
window.generatePlan=async function(){
  const mods=getSelectedMods();
  const errEl=$("cfg-error");
  if(errEl)errEl.style.display="none";
  if(!mods.length){
    if(errEl){errEl.textContent="Select at least one module.";errEl.style.display="";}
    return;
  }
  const name=($("s-name")||{}).value||"";
  const ttype=(document.querySelector('input[name="ttype"]:checked')||{}).value||"All";
  const genBtn=$("gen-btn");
  if(genBtn)genBtn.disabled=true;

  showRp("rp-planning");
  const logEl=$("plan-agent-log");
  if(logEl)logEl.innerHTML="";
  let elapsed=0;
  const eIv=setInterval(()=>setText("plan-elapsed",++elapsed+"s"),1000);
  addPlanLog("Contacting Claude API…","dim");

  try{
    const sess=await apiFetch("/sessions",{method:"POST",body:JSON.stringify({name,modules:mods,test_types:[ttype]})});
    _sid=sess.id;
    addPlanLog(`Session created: ${sess.id.slice(0,8)}…`,"info");

    // Upload scripts
    for(const f of _pendingFiles){
      const fd=new FormData();fd.append("file",f);
      await fetch(`${API}/sessions/${sess.id}/scripts/upload`,{method:"POST",body:fd}).catch(()=>{});
      addPlanLog(`Uploaded script: ${f.name}`,"info");
    }

    // Connect WS to see planning logs
    connectWs(sess.id,true);
    addPlanLog("Discovery Agent probing target API…","info");

    // Poll for plan
    for(let i=0;i<90;i++){
      await sleep(2000);
      const s=await apiFetch(`/sessions/${sess.id}`).catch(()=>null);
      if(!s)continue;
      if(s.state==="AWAITING_APPROVAL"){
        clearInterval(eIv);
        if(genBtn)genBtn.disabled=false;
        renderPlan(s.plan);
        break;
      }
      if(["FAILED","CANCELLED"].includes(s.state)){
        clearInterval(eIv);
        if(genBtn)genBtn.disabled=false;
        addPlanLog(`Session ${s.state}: ${s.error_message||""}`, "fail");
        break;
      }
    }
  }catch(e){
    clearInterval(eIv);
    if(genBtn)genBtn.disabled=false;
    if(errEl){errEl.textContent=e.message;errEl.style.display="";}
    showRp("rp-idle");
  }
};

function addPlanLog(msg,cls="info"){
  const el=$("plan-agent-log");if(!el)return;
  const d=document.createElement("div");
  const colors={info:"#475569",dim:"#94a3b8",fail:"#ef4444"};
  d.style.cssText=`font-size:12px;padding:4px 0;color:${colors[cls]||"#475569"};border-bottom:1px solid #f8fafc;`;
  d.textContent=`[${new Date().toLocaleTimeString()}] ${msg}`;
  el.appendChild(d);el.scrollTop=el.scrollHeight;
}

function renderPlan(plan){
  if(!plan){showRp("rp-idle");return;}
  showRp("rp-plan");
  setText("plan-case-count",(plan.total_cases||(plan.items||[]).length)+" test cases");
  setText("plan-summary",plan.ai_summary||"Plan generated by Discovery Agent.");
  const list=$("plan-items");if(!list)return;
  list.innerHTML=(plan.items||[]).map((item,i)=>{
    const tc=item.test_case||item;
    const name=tc.name||"—", mod=tc.module||"—", type=tc.test_type||"Functional";
    const typCls=type==="EdgeCase"?"p-amber":type==="Security"?"p-red":"p-blue";
    return `<div style="display:flex;align-items:flex-start;gap:8px;padding:9px 12px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;">
      <div style="width:20px;height:20px;border-radius:50%;background:#f1f5f9;color:#64748b;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px;">${i+1}</div>
      <div style="flex:1;min-width:0;">
        <div style="font-size:12px;font-weight:600;color:#0f172a;">${name}</div>
        <div style="display:flex;align-items:center;gap:5px;margin-top:3px;">
          <span style="font-size:11px;color:#64748b;">${mod}</span>
          <span class="pill ${typCls}" style="font-size:9px;">${type}</span>
        </div>
      </div>
    </div>`;
  }).join("");
}

window.approveAndRun=async function(){
  if(!_sid)return;
  await apiFetch(`/sessions/${_sid}/approve`,{method:"POST",body:JSON.stringify({message:"approved"})});
  _lPass=0;_lFail=0;_lErr=0;_lTotal=0;
  resetExecUI();
  showRp("rp-exec");
  _startT=new Date();startTimer();
};

window.rejectPlan=async function(){
  if(!_sid)return;
  const fb=prompt("What would you like changed in the plan?");
  if(!fb)return;
  await apiFetch(`/sessions/${_sid}/reject`,{method:"POST",body:JSON.stringify({feedback:fb})});
  showRp("rp-idle");loadIdleSessions();
};

window.sendPlanQ=async function(){
  const inp=$("plan-q");const msg=inp?inp.value.trim():"";if(!msg||!_sid)return;
  inp.value="";
  const qa=$("plan-qa");
  if(qa){qa.style.display="";qa.textContent="AI: Sending to session…";}
  await apiFetch(`/sessions/${_sid}/clarify`,{method:"POST",body:JSON.stringify({message:msg})}).catch(()=>{});
  if(qa)qa.textContent="AI: Your feedback has been noted. It will be considered if you regenerate the plan.";
};

// ── WebSocket ─────────────────────────────────────────────────────
function connectWs(sid,planMode=false){
  if(_ws){_ws.close();_ws=null;}
  _ws=new WebSocket(`${WS_BASE}/sessions/${sid}/ws`);
  _ws.onmessage=ev=>handleWs(JSON.parse(ev.data),planMode);
  _ws.onclose=()=>{if(!planMode)appendTerm("t-dim","[ws] connection closed");};
}

// Live UI stream state
let _uiFrameCount=0, _uiFpsStart=null, _uiCtx=null, _uiImg=null;

function ensureUiCanvas(){
  const c=$("ui-live"); if(!c) return null;
  if(!_uiCtx) _uiCtx=c.getContext("2d");
  if(!_uiImg){_uiImg=new Image();}
  return _uiCtx;
}

function showUiStreamPanel(){
  const w=$("ui-stream-wrap"); if(w) w.style.display="";
  if(!_uiFpsStart){_uiFpsStart=Date.now(); _uiFrameCount=0;}
}

function hideUiStreamPanel(){
  const w=$("ui-stream-wrap"); if(w) w.style.display="none";
}

function drawUiFrame(b64){
  showUiStreamPanel();
  const ctx=ensureUiCanvas(); if(!ctx) return;
  const img=new Image();
  img.onload=()=>{
    ctx.drawImage(img,0,0,1024,640);
  };
  img.src="data:image/jpeg;base64,"+b64;
  _uiFrameCount++;
  if(_uiFrameCount%6===0){
    const sec=(Date.now()-_uiFpsStart)/1000;
    setText("ui-stream-fps", (sec>0?(_uiFrameCount/sec).toFixed(1):"—")+" fps");
  }
}

function appendActionSnapshot(action, b64){
  showUiStreamPanel();
  const strip=$("ui-history-strip"); if(!strip) return;
  const row=document.createElement("div");
  row.style.cssText="display:flex;gap:6px;background:#0d1117;border:1px solid #1c2333;border-radius:5px;padding:5px;cursor:pointer;";
  row.title=action;
  row.onclick=()=>drawUiFrame(b64);  // click thumbnail -> re-render large
  row.innerHTML=`<img src="data:image/png;base64,${b64}" style="width:88px;height:auto;border-radius:3px;object-fit:cover;flex-shrink:0;" />
    <div style="flex:1;min-width:0;font-size:10px;color:#8b949e;line-height:1.4;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(action)}</div>`;
  strip.appendChild(row);
  strip.scrollTop=strip.scrollHeight;
}

// Debug panel state — collects EVERY ws event when enabled
let _debugEnabled=false;
window.toggleDebugPanel=function(){
  _debugEnabled=!_debugEnabled;
  setText("debug-toggle-state", _debugEnabled?"ON":"OFF");
};

function handleWs(envelope, planMode){
  const {type,data}=envelope;
  if(_debugEnabled){
    appendLog("t-dim", `[DEBUG] ${type}: ${JSON.stringify(data).slice(0,200)}`);
  }
  if(type==="log"){
    const l=(data.level||"").toLowerCase();
    const cls=l==="error"?"t-fail":l.startsWith("warn")?"t-warn":"t-info";
    if(planMode) addPlanLog(data.message, l==="error"?"fail":l.startsWith("warn")?"dim":"info");
    else         appendLog(cls,"["+data.agent+"] "+data.message);
  }else if(type==="test_result"){
    _lTotal++;
    if(data.status==="PASSED"){_lPass++;appendTerm("t-pass",`✓  ${data.test_name}  (${data.duration_ms}ms)`);}
    else if(data.status==="FAILED"){_lFail++;appendTerm("t-fail",`✗  ${data.test_name}  —  ${data.error||""}`); }
    else{_lErr++;appendTerm("t-warn",`⚠  ${data.test_name}  —  ${data.error||""}`);}
    updateExecProg();
  }else if(type==="state_change"){
    updateExecState(data.state);
    if(!planMode)appendLog("t-dim",`→ ${data.state}${data.detail?" | "+data.detail:""}`);
    if(["COMPLETED","FAILED","CANCELLED"].includes(data.state)){
      stopTimer();
      hideUiStreamPanel();
      apiFetch(`/sessions/${_sid}`).then(s=>showDone(s)).catch(()=>{});
    }
  }else if(type==="plan_ready"){
    if(planMode) addPlanLog(`Plan ready — ${data.total_cases} test cases`,"info");
  }else if(type==="clarification_request"){
    showClarif(data.question);
  }else if(type==="report_ready"){
    _doneReportId=data.report_id;
  }else if(type==="ui_frame"){
    drawUiFrame(data.frame);
  }else if(type==="ui_snapshot"){
    appendActionSnapshot(data.action, data.snapshot);
  }
}

function appendLog(cls,txt){
  const el=$("agent-log");if(!el)return;
  const d=document.createElement("div");d.className=cls;
  d.textContent=`[${new Date().toLocaleTimeString()}] ${txt}`;
  el.appendChild(d);el.scrollTop=el.scrollHeight;
}
function appendTerm(cls,txt){
  const el=$("test-out");if(!el)return;
  const d=document.createElement("div");d.className=cls;d.textContent=txt;
  el.appendChild(d);el.scrollTop=el.scrollHeight;
}
window.clrTerm=function(){const e=$("test-out");if(e)e.innerHTML="";};

function resetExecUI(){
  const al=$("agent-log"),to=$("test-out");
  if(al)al.innerHTML="";if(to)to.innerHTML="";
  setText("exec-passed",0);setText("exec-failed",0);setText("exec-errors",0);
  setText("exec-prog-lbl","0 of 0");
  const bar=$("exec-prog-bar");if(bar)bar.style.width="0%";
  hide($("clarif-panel"));
  // Reset live UI panel
  const strip=$("ui-history-strip"); if(strip) strip.innerHTML="";
  _uiFrameCount=0; _uiFpsStart=null;
  setText("ui-stream-fps", "— fps");
  hideUiStreamPanel();
}
function updateExecProg(){
  setText("exec-passed",_lPass);setText("exec-failed",_lFail);setText("exec-errors",_lErr);
  const done=_lPass+_lFail+_lErr;
  setText("exec-prog-lbl",done+" of "+_lTotal);
  const pct=_lTotal>0?Math.min(100,Math.round(done/_lTotal*100)):0;
  const bar=$("exec-prog-bar");if(bar){bar.style.width=pct+"%";}
}
function updateExecState(state){
  const p=$("exec-state-pill");if(!p)return;
  const m={EXECUTING:"p-blue",WAITING_FOR_INPUT:"p-amber",COMPLETED:"p-green",FAILED:"p-red",CANCELLED:"p-slate"};
  p.className="pill "+(m[state]||"p-slate");p.textContent=state;
}
function startTimer(){
  if(_timerIv)clearInterval(_timerIv);
  _timerIv=setInterval(()=>{
    if(!_startT)return;
    const s=Math.round((Date.now()-_startT.getTime())/1000);
    setText("exec-timer",String(Math.floor(s/60)).padStart(2,"0")+":"+String(s%60).padStart(2,"0"));
  },1000);
}
function stopTimer(){if(_timerIv){clearInterval(_timerIv);_timerIv=null;}}

function showClarif(q){
  const p=$("clarif-panel");if(!p)return;
  setText("clarif-q",q);p.style.display="";
}
window.sendClarif=async function(){
  const inp=$("clarif-ans");const msg=inp?inp.value.trim():"";
  if(!msg||!_sid)return;inp.value="";
  await apiFetch(`/sessions/${_sid}/clarify`,{method:"POST",body:JSON.stringify({message:msg})});
  hide($("clarif-panel"));
  appendLog("t-info","Clarification sent: "+msg);
};

function showDone(session){
  showRp("rp-done");
  const pass=session.results?.filter(r=>r.status==="PASSED").length??_lPass;
  const fail=session.results?.filter(r=>r.status==="FAILED").length??_lFail;
  const total=session.results?.length??(_lPass+_lFail+_lErr);
  const pct=total?Math.round(pass/total*100):0;
  const icon=$(  "done-icon"),title=$("done-title"),sub=$("done-subtitle");
  if(icon){
    icon.style.background=pct>=80?"#dcfce7":pct>=50?"#fef3c7":"#fee2e2";
    icon.textContent=pct>=80?"✓":pct>=50?"⚠":"✗";
  }
  if(title)title.style.color=pct>=80?"#15803d":pct>=50?"#b45309":"#dc2626";
  setText("done-title",pct>=80?"All tests passed!":pct>=50?"Tests completed with failures":"Critical failures detected");
  setText("done-subtitle",`${pass} passed · ${fail} failed · ${total} total · ${pct}% pass rate`);
  setText("done-passed",pass);setText("done-failed",fail);setText("done-total",total);
}
window.openDoneReport=function(){
  if(_doneReportId) openReportInline(_doneReportId, "Test Report");
  navigateTo("reports");
};
window.resetToIdle=function(){
  _sid=null;_lPass=0;_lFail=0;_lErr=0;_lTotal=0;_doneReportId=null;_pendingFiles=[];
  const sl=$("scripts-list");if(sl)sl.innerHTML="";
  showRp("rp-idle");loadIdleSessions();
};

// ══════════════════════════════════════════════════════════════════
// REPORTS
// ══════════════════════════════════════════════════════════════════
window.loadReports=async function(){
  const r=await apiFetch("/reports").catch(()=>({reports:[]}));
  const reps=r.reports||[];
  const tb=$("rpt-tbody"),em=$("rpt-empty");
  if(!reps.length){if(tb)tb.innerHTML="";if(em)em.style.display="";return;}
  if(em)em.style.display="none";
  tb.innerHTML=reps.map(rp=>{
    const pct=rp.total?Math.round(rp.passed/rp.total*100):0;
    const col=pct>=80?"#10b981":pct>=50?"#f59e0b":"#ef4444";
    return `<tr onclick="openReportInline('${rp.id}','${rp.session_name||rp.session_id.slice(0,8)}')">
      <td style="font-weight:600;">${rp.session_name||rp.session_id.slice(0,8)}</td>
      <td>
        <div style="display:flex;align-items:center;gap:7px;">
          <div class="prog-track" style="width:80px;"><div class="prog-fill" style="width:${pct}%;background:${col};"></div></div>
          <span style="font-size:12px;font-weight:700;color:${col};">${pct}%</span>
        </div>
      </td>
      <td><span style="color:#10b981;font-weight:700;">${rp.passed}</span></td>
      <td><span style="color:#ef4444;font-weight:700;">${rp.failed}</span></td>
      <td>${rp.total}</td>
      <td style="font-size:12px;color:#64748b;">${fmtDur(rp.duration_seconds)}</td>
      <td style="font-size:11px;color:#94a3b8;">${fmtDate(rp.created_at)}</td>
      <td style="color:#10b981;font-size:11px;font-weight:600;">View →</td>
    </tr>`;
  }).join("");
};

window.openReportInline=function(id,title){
  const v=$("rpt-viewer"),f=$("rpt-iframe");
  if(!v||!f)return;
  setText("rpt-viewer-title",title);
  f.src=`${API}/reports/${id}/html`;
  v.style.display="";
  v.scrollIntoView({behavior:"smooth"});
};

// ══════════════════════════════════════════════════════════════════
// GRAPHRAG
// ══════════════════════════════════════════════════════════════════
window.doSearch=async function(){
  const q=($("q-query")||{}).value?.trim();if(!q)return;
  const res=await apiFetch("/graphrag/search",{method:"POST",body:JSON.stringify({query:q,limit:5})}).catch(()=>({results:[]}));
  const c=$("q-results");if(!c)return;
  if(!res.results?.length){c.innerHTML=`<div style="font-size:12px;color:#94a3b8;text-align:center;padding:16px;">No results for "${q}"</div>`;return;}
  c.innerHTML=res.results.map(r=>{
    const col=r.level==="ERROR"?"#ef4444":r.level==="WARNING"?"#f59e0b":"#10b981";
    return `<div style="border:1px solid #e2e8f0;border-radius:8px;padding:10px;background:#fff;">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
        <span style="font-size:10px;font-weight:700;color:${col};background:${col}18;padding:1px 6px;border-radius:20px;">${r.level||"INFO"}</span>
        <span style="font-size:11px;color:#64748b;">${r.module||""}</span>
        <span class="mono" style="margin-left:auto;font-size:10px;color:#94a3b8;">${r.score}</span>
      </div>
      <div style="font-size:12px;color:#374151;">${r.message||""}</div>
      ${r.trace_id?`<div class="mono" style="font-size:10px;color:#94a3b8;margin-top:2px;">${r.trace_id}</div>`:""}
    </div>`;
  }).join("");
};

window.triggerIngest=async function(){
  await apiFetch("/graphrag/ingest",{method:"POST"}).catch(()=>{});
  setTimeout(loadQStatus,1200);
};
window.loadQStatus=async function(){
  const s=await apiFetch("/graphrag/status").catch(()=>null);if(!s)return;
  setText("q-status",`${s.qdrant?.points_count??0} vectors · ${s.ingestion?.running?"ingesting":"idle"} · last: ${s.ingestion?.last_ingested_at||"never"}`);
};

window.loadGraph=async function(){
  const data=await apiFetch("/graphrag/graph").catch(()=>({nodes:[],links:[]}));
  renderD3(data);
};

function renderD3({nodes=[],links=[]}){
  const box=$("d3-box");if(!box)return;
  box.innerHTML="";
  const W=box.clientWidth||500,H=box.clientHeight||400;
  const svg=d3.select(box).append("svg").attr("width",W).attr("height",H);
  const g=svg.append("g");
  svg.call(d3.zoom().scaleExtent([.3,3]).on("zoom",e=>g.attr("transform",e.transform)));

  svg.append("defs").append("marker").attr("id","arr").attr("viewBox","0 -4 8 8")
    .attr("refX",22).attr("refY",0).attr("markerWidth",5).attr("markerHeight",5).attr("orient","auto")
    .append("path").attr("d","M0,-4L8,0L0,4").attr("fill","#cbd5e1");

  const sim=d3.forceSimulation(nodes)
    .force("link",d3.forceLink(links).id(d=>d.id).distance(110))
    .force("charge",d3.forceManyBody().strength(-280))
    .force("center",d3.forceCenter(W/2,H/2));

  const link=g.append("g").selectAll("line").data(links).join("line")
    .attr("stroke","#e2e8f0").attr("stroke-width",1.5).attr("marker-end","url(#arr)");

  const node=g.append("g").selectAll("g").data(nodes).join("g")
    .call(d3.drag()
      .on("start",(e,d)=>{if(!e.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y;})
      .on("drag",(e,d)=>{d.fx=e.x;d.fy=e.y;})
      .on("end",(e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;})
    );

  node.append("circle").attr("r",28).attr("fill","#f0fdf9").attr("stroke","#10b981").attr("stroke-width",2);
  node.append("text").text(d=>d.id).attr("text-anchor","middle").attr("dy","0.35em")
    .attr("fill","#065f46").attr("font-size","11px").attr("font-weight","700")
    .attr("font-family","Inter, system-ui, sans-serif");
  node.append("title").text(d=>d.id);

  sim.on("tick",()=>{
    link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
        .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
    node.attr("transform",d=>`translate(${d.x},${d.y})`);
  });
}

// ══════════════════════════════════════════════════════════════════
// SETTINGS
// ══════════════════════════════════════════════════════════════════
window.loadSettings=async function(){
  const h=await fetch("/health").then(r=>r.json()).catch(()=>({}));
  const c=h.checks||{};
  const rows=[
    ["Target API URL","http://localhost:8000",c.target_api||"?"],
    ["Target UI URL","http://localhost:8080",null],
    ["Claude (Orchestration)","claude-opus-4-7","Agent Brain"],
    ["Claude (UI Tests)","claude-sonnet-4-6","Computer Use β"],
    ["Embedding Model","text-embedding-3-large · dim=3072","OpenAI"],
    ["Qdrant URL","http://localhost:6333",c.qdrant||"?"],
    ["Neo4j URI","bolt://localhost:7687",c.neo4j||"?"],
    ["Session Storage","aqe/data/sessions/ (JSON + RAM)",null],
    ["Reports Output","aqe/data/reports/ (JSON + HTML)",null],
  ];
  const box=$("cfg-rows");if(!box)return;
  box.innerHTML=rows.map(([label,val,status])=>{
    const badge=status?`<span class="pill ${status==="ok"?"p-green":["Agent Brain","Computer Use β","OpenAI"].includes(status)?"p-violet":"p-amber"}">${status}</span>`:"";
    return `<div style="display:flex;align-items:center;justify-content:space-between;padding:13px 18px;border-bottom:1px solid #f1f5f9;">
      <div>
        <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#94a3b8;margin-bottom:2px;">${label}</div>
        <div class="mono" style="font-size:12px;color:#374151;">${val}</div>
      </div>
      ${badge}
    </div>`;
  }).join("");
};

// ── Bootstrap ─────────────────────────────────────────────────────
navigateTo("dashboard");
pollStatus();
setInterval(pollStatus,10000);
})();
