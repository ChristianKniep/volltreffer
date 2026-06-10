"use strict";
const APP_VERSION = "v11";   // bump together with the ?v= cache-bust in index.html
const HEAT = {5:'#B3122B',4:'#E0561F',3:'#E59020',2:'#C7A63C',1:'#9B9082'};
const GROUP_ORDER = "ABCDEFGHIJKL".split("");
const ROUND_LABEL = {R32:"Round of 32",R16:"Round of 16",QF:"Quarter-finals",SF:"Semi-finals",FINAL:"Final","3RD":"Third place"};
const fmtEl = (s)=>{const d=document.createElement("div");d.textContent=s;return d.innerHTML;};
const flag = (iso)=>iso?`<img class="fl" src="/static/flags/${iso}.svg" alt="">`:"";
let STATE = null;
let ME = null;
let ACTIVE_PROVIDER = localStorage.getItem("wc_provider") || "";

async function getState(){
  const q = ACTIVE_PROVIDER ? `?provider=${encodeURIComponent(ACTIVE_PROVIDER)}` : "";
  const r = await fetch("/api/state"+q);
  if(r.status===401){ showGate(); return; }
  STATE = await r.json();
  ACTIVE_PROVIDER = STATE.meta.active_provider || "";
  renderAll();
}
async function postResult(id, body){
  const r = await fetch(`/api/match/${id}/result`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  if(!r.ok){ toast("Couldn't save that result."); return; }
  await getState(); toast("Result saved — bracket updated.");
}
async function runUpdate(){
  toast("Checking for results…");
  const r = await fetch("/api/update",{method:"POST"});
  if(r.status===401){ showGate(); return; }
  const s = await r.json();
  const provs = s.providers || {};
  const parts = Object.entries(provs).map(([pid,o])=>
    o.synced ? `${pid}: ${o.synced} synced` : (o.errors&&o.errors.length ? `${pid}: ${o.errors[0]}` : "")
  ).filter(Boolean);
  const provMsg = parts.length ? " · " + parts.join(" · ") : "";
  if(s.errors && s.errors.length) toast(s.errors[0] + provMsg);
  else toast(`${s.source}: ${s.updated} updated, ${s.skipped} skipped${provMsg}`);
  await getState();
}

/* ---------- helpers ---------- */
function teamSlot(name, ref, iso, score, pens, isWinner){
  const inner = name
    ? `${flag(iso)}<span class="nm">${fmtEl(name)}</span>`
    : `<span class="ph">${fmtEl(ref)}</span>`;
  const sc = score==null ? "" :
    `<span class="sc">${score}${pens!=null?` <span class="pen">(${pens})</span>`:""}</span>`;
  return `<div class="side ${isWinner?'win':''}">${inner}${sc}</div>`;
}
function predBar(p, ko, tip){
  if(!p) return "";
  let bar, key;
  if(ko){
    bar = `<div class="bar ko"><i class="h" style="width:${p.adv_home}%"></i><i class="a" style="width:${p.adv_away}%"></i></div>`;
    key = `<div class="barkey"><span>${p.adv_home}% advance</span><span>${p.adv_away}%</span></div>`;
  }else{
    bar = `<div class="bar"><i class="h" style="width:${p.p_home}%"></i><i class="d" style="width:${p.p_draw}%"></i><i class="a" style="width:${p.p_away}%"></i></div>`;
    key = `<div class="barkey"><span>${p.p_home}% W</span><span>${p.p_draw}% D</span><span>${p.p_away}% W</span></div>`;
  }
  const tipChip = tip
    ? `<span class="tipchip ${tip.differs?'diff':'ok'}" title="Your teamtip tip"><i>teamtip</i>${tip.home}–${tip.away}</span>`
    : `<span class="tipchip none" title="No teamtip tip synced"><i>teamtip</i>–</span>`;
  return `<div class="pred"><div class="ttl">Prediction</div>
    <div class="line">
      <span class="ps" title="Model prediction"><i>model</i>${p.score_home}–${p.score_away}</span>
      ${tipChip}
      <span class="rat">${fmtEl(p.rationale)}</span>
    </div>
    ${bar}${key}</div>`;
}
function editor(m){
  const ko = m.stage==="ko";
  const pens = ko ? `<label>pens</label>
     <input type="number" min="0" class="hp" placeholder="–" value="${m.home_pens??""}">
     <input type="number" min="0" class="ap" placeholder="–" value="${m.away_pens??""}">` : "";
  // teamtip tip entry — only for upcoming matches with resolved teams.
  // default to your existing teamtip bet, else the model's predicted score.
  const canTip = m.home && m.away && m.status!=='finished';
  const tipH = m.tip ? m.tip.home : (m.prediction ? m.prediction.score_home : "");
  const tipA = m.tip ? m.tip.away : (m.prediction ? m.prediction.score_away : "");
  const tipForm = canTip ? `
    <label class="ttlbl">Your teamtip tip ${m.tip?'<span class="src">(synced bet)</span>':'<span class="src">(model default)</span>'}</label>
    <div class="eg tte">
      <span>${fmtEl(m.home)}</span>
      <input type="number" min="0" class="tth" value="${tipH}">
      <span>–</span>
      <input type="number" min="0" class="tta" value="${tipA}">
      <span>${fmtEl(m.away)}</span>
      <button class="btn tt-save">Save to teamtip</button>
    </div>` : "";
  return `<div class="editor" data-id="${m.id}">
    <label>Enter result</label>
    <div class="eg">
      <span>${fmtEl(m.home||m.home_ref)}</span>
      <input type="number" min="0" class="hs" value="${m.home_score??""}">
      <span>–</span>
      <input type="number" min="0" class="as" value="${m.away_score??""}">
      <span>${fmtEl(m.away||m.away_ref)}</span>
      ${pens}
      <button class="btn save">Save result</button>
      <button class="btn clr">Cancel</button>
    </div>${tipForm}</div>`;
}

/* ---------- schedule ---------- */
// kickoff_et is US Eastern wall-clock (EDT, UTC-4) for the whole tournament window
function kickoffMs(m){
  const [d,t] = m.kickoff_et.split(" ");
  return Date.parse(`${d}T${t}:00Z`) + 4*3600*1000;   // shift ET -> real UTC
}
function dayGridHTML(entries){
  return entries.map(([d,ms])=>`
    <div class="day">
      <h3>${d.split(" ")[0]}<span class="dt">${d.split(" ").slice(1).join(" ")}</span><span class="n">${ms.length}</span></h3>
      ${ms.map(matchCard).join("")}
    </div>`).join("");
}
function byDateEntries(list){
  const o = {};
  for(const m of list){ (o[m.cest_date] ||= []).push(m); }
  return Object.entries(o);
}
function renderSchedule(){
  document.getElementById("fkey").innerHTML =
    [1,2,3,4,5].map(i=>`<span class="fpip" style="background:${HEAT[i]}">🔥${i}</span>`).join("");
  const now = Date.now();
  const isPast = (m)=> m.status==='finished' || kickoffMs(m) <= now;
  const upcoming = STATE.matches.filter(m=>!isPast(m));
  const past = STATE.matches.filter(isPast);
  document.getElementById("schedUpcoming").innerHTML = upcoming.length
    ? dayGridHTML(byDateEntries(upcoming)) : `<p class="note">No upcoming matches.</p>`;
  // past: most-recently-played day first
  document.getElementById("schedPast").innerHTML = past.length
    ? dayGridHTML(byDateEntries(past).reverse()) : `<p class="note">No matches played yet.</p>`;
  document.getElementById("cntUp").textContent = upcoming.length;
  document.getElementById("cntPast").textContent = past.length;
  bindCards();
}
function tipLine(m){
  if(!m.tip) return "";
  const differs = m.tip.differs;
  const note = m.status==='finished' ? "" :
    (differs ? ` <span class="tipnote">differs from model</span>` : ` <span class="tipnote ok">matches model</span>`);
  return `<div class="tip ${differs?'diff':''}"><span class="ttl">Your teamtip tip</span>
    <b>${m.tip.home}–${m.tip.away}</b>${note}</div>`;
}
function matchCard(m){
  const ko = m.stage==="ko";
  const winH = m.winner && m.winner===m.home, winA = m.winner && m.winner===m.away;
  const chipColor = ko ? "#6b6256" : (STATE.groups[m.group]?.color||"#888");
  const chipText = ko ? m.round : m.group;
  return `<div class="m ${m.status==='finished'?'played':''}" style="--heat:${m.excitement.color}" tabindex="0" data-id="${m.id}">
    <div class="row1">
      <div class="time" style="background:${m.time_color}"><div class="c">${m.cest_time}</div><div class="e">${m.et_time} ET</div></div>
      <div class="fix">
        ${teamSlot(m.home,m.home_ref,m.home_iso,m.home_score,m.home_pens,winH)}
        ${teamSlot(m.away,m.away_ref,m.away_iso,m.away_score,m.away_pens,winA)}
      </div>
      <div class="exc">🔥<b>${m.excitement.tier}</b></div>
    </div>
    <div class="meta-line">
      <span class="chip" style="background:${chipColor}">${chipText}</span>
      <span class="venue">${fmtEl(m.venue)} · ${fmtEl(m.city)}</span>
    </div>
    ${m.status!=='finished' ? predBar(m.prediction, ko, m.tip) : ""}
    ${m.status==='finished' ? tipLine(m) : ""}
    ${editor(m)}
  </div>`;
}

/* ---------- groups ---------- */
function renderGroups(){
  const box = document.getElementById("groupGrid");
  box.innerHTML = GROUP_ORDER.map(g=>{
    const G = STATE.groups[g];
    const rows = G.standings.map((t,i)=>`
      <tr class="${i<2?('q'+(i+1)):(i===2?'q3':'')}">
        <td>${t.rank}</td>
        <td class="team">${flag(t.iso)}<span>${fmtEl(t.team)}</span></td>
        <td>${t.P}</td><td>${t.W}</td><td>${t.D}</td><td>${t.L}</td>
        <td>${t.GD>0?'+':''}${t.GD}</td><td class="pts">${t.Pts}</td>
      </tr>`).join("");
    const gm = STATE.matches.filter(m=>m.group===g);
    const mhead = `<div class="mini mhead">
      <span class="mt-date"></span><span class="mt-teams"></span>
      <span title="Model prediction (most likely score)">Pred</span>
      <span title="Actual result">Res</span>
      <span title="Your teamtip tip">Tip</span></div>`;
    return `<div class="gcard">
      <h3 style="background:${G.color}"><span class="gl">${g}</span> Group ${g}
        <span class="st">${G.complete?"complete":"in progress"}</span></h3>
      <table class="tbl"><thead><tr><th>#</th><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GD</th><th>Pts</th></tr></thead>
        <tbody>${rows}</tbody></table>
      <div class="gmatches">${mhead}${gm.map(miniMatch).join("")}</div>
    </div>`;
  }).join("");
}
function miniMatch(m){
  const finished = m.status==='finished';
  const pred = m.prediction ? `${m.prediction.score_home}–${m.prediction.score_away}` : "–";
  const result = finished ? `${m.home_score}–${m.away_score}` : "–";
  const tip = m.tip ? `${m.tip.home}–${m.tip.away}` : "–";
  // how the teamtip tip fared once a result is in (Kicktipp-style: exact / tendency / miss)
  let tipCls = "";
  if(m.tip && finished){
    const exact = m.tip.home===m.home_score && m.tip.away===m.away_score;
    const sameTend = (m.tip.home-m.tip.away===m.home_score-m.away_score)
      || (m.tip.home>m.tip.away && m.home_score>m.away_score)
      || (m.tip.home<m.tip.away && m.home_score<m.away_score);
    tipCls = exact ? "hit" : (sameTend ? "tend" : "miss");
  }
  const date = `${m.cest_date.split(" ").slice(1).join(" ")} ${m.cest_time}`;
  return `<div class="mini" data-id="${m.id}" title="${fmtEl(m.home)} v ${fmtEl(m.away)}">
    <span class="mt-date">${date}</span>
    <span class="mt-teams">${fmtEl(m.home)} v ${fmtEl(m.away)}</span>
    <span class="mtag pred">${pred}</span>
    <span class="mtag res ${finished?'has':''}">${result}</span>
    <span class="mtag tip ${tipCls}">${tip}</span>
  </div>`;
}

/* ---------- bracket ---------- */
function renderBracket(){
  const cols = ["R32","R16","QF","SF","FINAL"];
  const box = document.getElementById("bracketBox");
  box.innerHTML = cols.map(rnd=>{
    let ties = STATE.matches.filter(m=>m.round===rnd).sort((a,b)=>a.match_no-b.match_no);
    let extra = "";
    if(rnd==="FINAL"){
      const third = STATE.matches.find(m=>m.round==="3RD");
      if(third) extra = `<div style="margin-top:18px"><h4>Third place</h4>${tieCard(third)}</div>`;
    }
    return `<div class="bcol ${rnd==='FINAL'?'final':''}">
      <h4>${ROUND_LABEL[rnd]}</h4>
      <div class="ties">${ties.map(tieCard).join("")}</div>${extra}</div>`;
  }).join("");
  bindCards();
}
function tieCard(m){
  const ko=true;
  const advH = m.winner ? m.winner===m.home : (m.prediction && m.prediction.favored==="home");
  const advA = m.winner ? m.winner===m.away : (m.prediction && m.prediction.favored==="away");
  const pl = m.status!=='finished' && m.prediction
    ? `<div class="pl"><b>${m.prediction.score_home}–${m.prediction.score_away}</b> · ${fmtEl(m.prediction.rationale)}</div>` : "";
  return `<div class="tie ${m.round==='FINAL'?'finalt':''}" tabindex="0" data-id="${m.id}">
    <div class="no">Match ${m.match_no} · ${m.venue?fmtEl(m.city):""}</div>
    <div class="tslot ${advH?'adv':''}">${m.home?flag(m.home_iso):""}${m.home?`<span class="nm">${fmtEl(m.home)}</span>`:`<span class="ph">${fmtEl(m.home_ref)}</span>`}${m.home_score!=null?`<span class="sc">${m.home_score}${m.home_pens!=null?` (${m.home_pens})`:""}</span>`:""}</div>
    <div class="tslot ${advA?'adv':''}">${m.away?flag(m.away_iso):""}${m.away?`<span class="nm">${fmtEl(m.away)}</span>`:`<span class="ph">${fmtEl(m.away_ref)}</span>`}${m.away_score!=null?`<span class="sc">${m.away_score}${m.away_pens!=null?` (${m.away_pens})`:""}</span>`:""}</div>
    ${pl}${tipLine(m)}${editor(m)}</div>`;
}

/* ---------- interactions ---------- */
function bindCards(){
  document.querySelectorAll(".m,.tie,.mini").forEach(el=>{
    el.onclick = (e)=>{ if(e.target.closest(".editor")) return; toggleEditor(el); };
    el.onkeydown = (e)=>{ if(e.key==="Enter"||e.key===" "){e.preventDefault(); toggleEditor(el);} };
  });
  document.querySelectorAll(".editor").forEach(ed=>{
    ed.querySelector(".save").onclick = (e)=>{ e.stopPropagation(); saveFrom(ed); };
    ed.querySelector(".clr").onclick = (e)=>{ e.stopPropagation(); ed.classList.remove("open"); };
    const tt = ed.querySelector(".tt-save");
    if(tt) tt.onclick = (e)=>{ e.stopPropagation(); submitTip(ed); };
  });
}
function toggleEditor(card){
  const id = card.dataset.id;
  const ed = card.querySelector(`.editor[data-id="${id}"]`) || document.querySelector(`.editor[data-id="${id}"]`);
  if(ed) ed.classList.toggle("open");
}
async function submitTip(ed){
  const h = ed.querySelector(".tth").value, a = ed.querySelector(".tta").value;
  if(h===""||a===""){ toast("Enter both tip scores."); return; }
  if(!ACTIVE_PROVIDER){ toast("Add a betting backend in Settings first."); return; }
  const label = (STATE.meta.providers.find(p=>p.id===ACTIVE_PROVIDER)||{}).label || ACTIVE_PROVIDER;
  toast(`Sending tip to ${label}…`);
  const r = await fetch(`/api/match/${ed.dataset.id}/tip`,{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({home:+h,away:+a,provider:ACTIVE_PROVIDER})});
  if(!r.ok){ const e = await r.json().catch(()=>({})); toast(label+": "+(e.detail||"submit failed")); return; }
  await getState(); toast(`Tip ${h}–${a} saved to ${label}.`);
}
function saveFrom(ed){
  const hs = ed.querySelector(".hs").value, as = ed.querySelector(".as").value;
  if(hs===""||as===""){ toast("Enter both scores."); return; }
  const body = {home_score:+hs, away_score:+as};
  const hp = ed.querySelector(".hp"), ap = ed.querySelector(".ap");
  if(hp && hp.value!=="") body.home_pens = +hp.value;
  if(ap && ap.value!=="") body.away_pens = +ap.value;
  postResult(ed.dataset.id, body);
}

function renderMeta(){
  const m = STATE.meta;
  const lu = m.last_update==="never" ? "no results yet" : `updated ${m.last_update.replace("T"," ").replace("Z"," UTC")}`;
  document.getElementById("metaMini").innerHTML = `<b>${m.finished}</b>/${m.total} played · ${lu}`;
  renderProviderSelector();
}
function renderProviderSelector(){
  const provs = STATE.meta.providers || [];
  const wrap = document.getElementById("provSelWrap");
  const sel = document.getElementById("provSel");
  if(provs.length < 2){ wrap.hidden = true; return; }   // only useful with 2+ backends
  wrap.hidden = false;
  sel.innerHTML = provs.map(p=>`<option value="${p.id}" ${p.id===ACTIVE_PROVIDER?"selected":""}>${fmtEl(p.label)}</option>`).join("");
}
function renderAll(){ renderMeta(); renderSchedule(); renderGroups(); renderBracket(); }

/* ---------- auth ---------- */
let AUTH_MODE = "login";
function showGate(){
  ME = null;
  document.getElementById("authForm").hidden = false;
  document.getElementById("pendingCard").hidden = true;
  document.getElementById("authGate").hidden = false;
  document.getElementById("userBox").hidden = true;
  document.getElementById("provSelWrap").hidden = true;
  document.getElementById("adminTab").hidden = true;
}
function showPending(name){
  document.getElementById("pendingName").textContent = name || "";
  document.getElementById("authForm").hidden = true;
  document.getElementById("pendingCard").hidden = false;
  document.getElementById("authGate").hidden = false;
  document.getElementById("userBox").hidden = true;
  document.getElementById("provSelWrap").hidden = true;
  document.getElementById("adminTab").hidden = true;
}
function hideGate(){ document.getElementById("authGate").hidden = true; }
function setAuthMode(mode){
  AUTH_MODE = mode;
  const login = mode==="login";
  document.getElementById("authSub").textContent = login
    ? "Sign in to see your predictions and tips" : "Create an account for your own tips";
  document.getElementById("authSubmit").textContent = login ? "Log in" : "Create account";
  document.getElementById("authSwitchText").textContent = login ? "No account yet?" : "Already registered?";
  document.getElementById("authSwitch").textContent = login ? "Create one" : "Log in";
  document.getElementById("authPass").autocomplete = login ? "current-password" : "new-password";
  document.getElementById("authErr").textContent = "";
}
async function submitAuth(e){
  e.preventDefault();
  const username = document.getElementById("authUser").value.trim();
  const password = document.getElementById("authPass").value;
  const err = document.getElementById("authErr");
  err.textContent = "";
  const r = await fetch(`/api/auth/${AUTH_MODE==="login"?"login":"register"}`,{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify({username,password})});
  const d = await r.json().catch(()=>({}));
  if(!r.ok){ err.textContent = d.detail || "Something went wrong."; return; }
  ME = d;
  if(!d.approved){ showPending(d.username); return; }
  hideGate(); afterLogin();
}
async function checkAuth(){
  const d = await (await fetch("/api/auth/me")).json();
  if(d.authenticated && d.approved){ ME = d; hideGate(); afterLogin(); }
  else if(d.authenticated){ ME = d; showPending(d.username); }
  else {
    if(!d.registration_open){
      document.getElementById("authSwitch").style.display = "none";
      document.getElementById("authSwitchText").textContent = "Ask the admin for an account.";
    }
    showGate();
  }
}
function afterLogin(){
  const box = document.getElementById("userBox");
  box.hidden = false;
  document.getElementById("whoami").textContent = ME.username + (ME.is_admin?" · admin":"");
  document.getElementById("adminTab").hidden = !ME.is_admin;
  getState();
  loadSettings();
  if(ME.is_admin) loadAdmin();
}
async function logout(){
  await fetch("/api/auth/logout",{method:"POST"});
  location.reload();
}

/* ---------- settings: betting backends ---------- */
async function loadSettings(){
  const r = await fetch("/api/providers");
  if(!r.ok) return;
  const { providers } = await r.json();
  document.getElementById("provCards").innerHTML = providers.map(provCard).join("");
  bindProvCards();
}
function provField(pid, f, val){
  const type = f.type==="password" ? "password" : (f.type==="number" ? "number" : "text");
  return `<label class="pf">
    <span>${fmtEl(f.label)}${f.required?'':' <i>(optional)</i>'}</span>
    <input data-field="${f.name}" type="${type}" value="${f.secret?'':fmtEl(val||'')}"
      placeholder="${fmtEl(f.placeholder||'')}" autocomplete="off">
    ${f.help?`<small>${fmtEl(f.help)}</small>`:""}
  </label>`;
}
function provCard(p){
  const status = p.configured
    ? `<span class="pstat ok">connected</span>`
    : `<span class="pstat">not connected</span>`;
  return `<div class="provcard" data-pid="${p.id}">
    <div class="phead"><h3>${fmtEl(p.label)}</h3>${status}</div>
    <p class="pblurb">${fmtEl(p.blurb||"")}</p>
    <div class="pfields">${p.fields.map(f=>provField(p.id,f,p.values[f.name])).join("")}</div>
    <div class="pmsg"></div>
    <div class="pacts">
      <button class="btn primary p-save">Save &amp; test</button>
      ${p.configured?`<button class="btn ghost p-del">Disconnect</button>`:""}
    </div>
  </div>`;
}
function bindProvCards(){
  document.querySelectorAll(".provcard").forEach(card=>{
    const pid = card.dataset.pid;
    const msg = card.querySelector(".pmsg");
    card.querySelector(".p-save").onclick = async ()=>{
      const body = {};
      card.querySelectorAll("input[data-field]").forEach(i=>{ if(i.value!=="") body[i.dataset.field]=i.value; });
      msg.textContent = "Saving…"; msg.className = "pmsg";
      const r = await fetch(`/api/providers/${pid}/credentials`,{method:"PUT",
        headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const d = await r.json().catch(()=>({}));
      if(!r.ok){ msg.textContent = d.detail||"Save failed."; msg.className="pmsg err"; return; }
      msg.textContent = d.valid ? (d.message||"Connected.") : ("Saved, but: "+(d.message||"could not verify"));
      msg.className = "pmsg "+(d.valid?"ok":"warn");
      await loadSettings(); await getState();
    };
    const del = card.querySelector(".p-del");
    if(del) del.onclick = async ()=>{
      await fetch(`/api/providers/${pid}/credentials`,{method:"DELETE"});
      if(ACTIVE_PROVIDER===pid){ ACTIVE_PROVIDER=""; localStorage.removeItem("wc_provider"); }
      await loadSettings(); await getState(); toast("Disconnected.");
    };
  });
}

/* ---------- admin: user management ---------- */
async function loadAdmin(){
  const r = await fetch("/api/admin/users");
  if(!r.ok) return;
  const { users } = await r.json();
  const pending = users.filter(u=>!u.approved).length;
  document.getElementById("adminTab").textContent = pending ? `Admin (${pending})` : "Admin";
  document.getElementById("userTable").innerHTML = `
    <table class="utbl">
      <thead><tr><th>User</th><th>Joined</th><th>Role</th><th>Status</th><th></th></tr></thead>
      <tbody>${users.map(userRow).join("")}</tbody>
    </table>`;
  bindUserRows();
}
function userRow(u){
  const joined = (u.created_at||"").replace("T"," ").replace("Z","").slice(0,16);
  const roleSel = `<select class="urole" ${u.is_self?"disabled":""}>
    <option value="user" ${u.role==="user"?"selected":""}>user</option>
    <option value="admin" ${u.role==="admin"?"selected":""}>admin</option></select>`;
  const status = u.approved
    ? `<span class="pstat ok">approved</span>`
    : `<span class="pstat pend">pending</span>`;
  const actions = [
    u.approved ? "" : `<button class="btn u-approve">Approve</button>`,
    u.is_self ? `<span class="self">you</span>` : `<button class="btn ghost u-del">Remove</button>`,
  ].filter(Boolean).join("");
  return `<tr data-uid="${u.id}" class="${u.approved?'':'pendrow'}">
    <td class="uname">${fmtEl(u.username)}</td>
    <td class="ujoin">${fmtEl(joined)}</td>
    <td>${roleSel}</td>
    <td>${status}</td>
    <td class="uacts">${actions}</td>
  </tr>`;
}
function bindUserRows(){
  document.querySelectorAll("#userTable tr[data-uid]").forEach(tr=>{
    const uid = tr.dataset.uid;
    const ap = tr.querySelector(".u-approve");
    if(ap) ap.onclick = ()=>adminAction(`/api/admin/users/${uid}/approve`,{method:"POST"},"Approved.");
    const del = tr.querySelector(".u-del");
    if(del) del.onclick = ()=>{ if(confirm("Remove this user and their tips?"))
      adminAction(`/api/admin/users/${uid}`,{method:"DELETE"},"User removed."); };
    const role = tr.querySelector(".urole");
    if(role) role.onchange = ()=>adminAction(`/api/admin/users/${uid}/role`,
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({role:role.value})},
      `Role set to ${role.value}.`);
  });
}
async function adminAction(url, opts, okMsg){
  const r = await fetch(url, opts);
  const d = await r.json().catch(()=>({}));
  if(!r.ok){ toast(d.detail||"Action failed."); }
  else toast(okMsg);
  await loadAdmin();
}

function toast(msg){
  const t=document.getElementById("toast"); t.textContent=msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t=setTimeout(()=>t.classList.remove("show"),3200);
}

document.querySelectorAll("nav.tabs button").forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll("nav.tabs button").forEach(x=>x.setAttribute("aria-selected", x===b));
    document.querySelectorAll("section.view").forEach(v=>v.classList.toggle("active", v.id===b.dataset.view));
  };
});
document.querySelectorAll("nav.subtabs button").forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll("nav.subtabs button").forEach(x=>x.setAttribute("aria-selected", x===b));
    document.getElementById("schedUpcoming").classList.toggle("active", b.dataset.sub==="upcoming");
    document.getElementById("schedPast").classList.toggle("active", b.dataset.sub==="past");
  };
});
document.getElementById("updateBtn").onclick = runUpdate;
document.getElementById("appVer").textContent = APP_VERSION;

// auth + provider wiring
document.getElementById("authForm").addEventListener("submit", submitAuth);
document.getElementById("authSwitch").addEventListener("click", (e)=>{
  e.preventDefault(); setAuthMode(AUTH_MODE==="login"?"register":"login");
});
document.getElementById("logoutBtn").onclick = logout;
document.getElementById("pendingLogout").onclick = logout;
document.getElementById("provSel").onchange = (e)=>{
  ACTIVE_PROVIDER = e.target.value;
  localStorage.setItem("wc_provider", ACTIVE_PROVIDER);
  getState();
};
setAuthMode("login");
checkAuth();
