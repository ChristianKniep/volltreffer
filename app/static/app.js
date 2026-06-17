"use strict";
const APP_VERSION = "v23";   // bump together with the ?v= cache-bust in index.html
const HEAT = {5:'#B3122B',4:'#E0561F',3:'#E59020',2:'#C7A63C',1:'#9B9082'};
const GROUP_ORDER = "ABCDEFGHIJKL".split("");
const ROUND_LABEL = {R32:"Round of 32",R16:"Round of 16",QF:"Quarter-finals",SF:"Semi-finals",FINAL:"Final","3RD":"Third place"};
const fmtEl = (s)=>{const d=document.createElement("div");d.textContent=s;return d.innerHTML;};
const attrEsc = (s)=>String(s==null?"":s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
const flag = (iso)=>iso?`<img class="fl" src="/static/flags/${iso}.svg" alt="">`:"";
function tvBadge(b){
  if(!b) return "";
  const cls = b==="ARD" ? "ard" : (b==="ZDF" ? "zdf" : "both");
  return `<span class="tv ${cls}" title="Live im Free-TV: ${attrEsc(b)}">📺 ${fmtEl(b)}</span>`;
}
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
  for(const k in TIP_CACHE) delete TIP_CACHE[k];   // results/tips changed → re-fetch on next hover
  loadLeaderboard();
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
  const ov = p.overridden, you = p.override_source==='you';
  const ovLabel = you ? 'your view' : 'set';
  const psLabel = you ? 'you' : (ov ? 'set' : 'model');
  const psTitle = you ? 'Your own prediction' : (ov ? 'Shared override' : 'Model prediction');
  const rat = p.rationale || "";
  const wrap = `<span class="predwrap"${rat?` data-tip="${attrEsc(rat)}"`:''}>
      <span class="ps ${ov?'ov':''}" title="${psTitle}"><i>${psLabel}</i>${p.score_home}–${p.score_away}</span>
      ${rat?'<span class="rathint">why?</span>':''}
    </span>`;
  return `<div class="pred"><div class="ttl">Prediction${ov?` <span class="ovtag" title="${psTitle}">${ovLabel}</span>`:''}</div>
    <div class="line">
      ${wrap}
      ${tipChip}
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
  for(const m of list){ (o[m.local_date] ||= []).push(m); }
  return Object.entries(o);
}
function renderSchedule(){
  document.getElementById("fkey").innerHTML =
    [1,2,3,4,5].map(i=>`<span class="fpip" style="background:${HEAT[i]}">🔥${i}</span>`).join("");
  document.getElementById("tzLab").textContent = "Kick-off (" + (STATE.meta.timezone || "local") + ")";
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
      <div class="time" style="background:${m.time_color}" title="Slot rating ${m.time_rating}/5 (${m.tz_abbr}) · kickoff ${m.venue_time} ${m.venue_tz_abbr} at the venue"><div class="c">${m.local_time}</div><div class="e">${m.venue_time} ${m.venue_tz_abbr}</div></div>
      <div class="fix">
        ${teamSlot(m.home,m.home_ref,m.home_iso,m.home_score,m.home_pens,winH)}
        ${teamSlot(m.away,m.away_ref,m.away_iso,m.away_score,m.away_pens,winA)}
      </div>
      <div class="exc">🔥<b>${m.excitement.tier}</b></div>
    </div>
    <div class="meta-line">
      <span class="chip" style="background:${chipColor}">${chipText}</span>
      <span class="venue">${fmtEl(m.venue)} · ${fmtEl(m.city)}</span>
      ${tvBadge(m.broadcaster)}
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
  const date = `${m.local_date.split(" ").slice(1).join(" ")} ${m.local_time}`;
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
  loadToken();
  loadPrefs();
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
  let tokNote = "";
  if(p.token_exp){
    const d = new Date(p.token_exp*1000);
    const when = d.toLocaleDateString(undefined,{year:"numeric",month:"short",day:"numeric"});
    tokNote = p.token_expired
      ? `<p class="toknote err">Token expired — paste a fresh one to keep syncing.</p>`
      : `<p class="toknote">Token valid until <b>${when}</b>.</p>`;
  }
  const groupBtn = (p.id==="teamtip" && p.configured)
    ? `<button class="btn ghost p-group">Sync group →</button>` : "";
  return `<div class="provcard" data-pid="${p.id}">
    <div class="phead"><h3>${fmtEl(p.label)}</h3>${status}</div>
    <p class="pblurb">${fmtEl(p.blurb||"")}</p>
    ${tokNote}
    <div class="pfields">${p.fields.map(f=>provField(p.id,f,p.values[f.name])).join("")}</div>
    <div class="pmsg"></div>
    <div class="pacts">
      <button class="btn primary p-save">Save &amp; test</button>
      ${groupBtn}
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
    const grp = card.querySelector(".p-group");
    if(grp) grp.onclick = async ()=>{
      grp.disabled = true;
      msg.textContent = "Importing group…"; msg.className = "pmsg";
      try{
        const r = await fetch("/api/teamtip/sync-group",{method:"POST"});
        const d = await r.json().catch(()=>({}));
        if(!r.ok){ msg.textContent = d.detail||"Group sync failed."; msg.className="pmsg err"; toast(d.detail||"Group sync failed."); return; }
        if(d.errors && d.errors.length){ msg.textContent = d.errors.join(" "); msg.className="pmsg warn"; toast(d.errors[0]); }
        else { msg.textContent = `Imported ${d.members} players, ${d.tips} tips.`; msg.className="pmsg ok"; toast(`teamtip group synced: ${d.members} players, ${d.tips} tips.`); }
        // refresh every view that shows players
        await Promise.all([loadLeaderboard(), getState()]);
        if(MD_LIST) await loadMatchdays();
      } finally { grp.disabled = false; }
    };
    const del = card.querySelector(".p-del");
    if(del) del.onclick = async ()=>{
      await fetch(`/api/providers/${pid}/credentials`,{method:"DELETE"});
      if(ACTIVE_PROVIDER===pid){ ACTIVE_PROVIDER=""; localStorage.removeItem("wc_provider"); }
      await loadSettings(); await getState(); toast("Disconnected.");
    };
  });
}

/* ---------- personal API token ---------- */
function showToken(tok){
  const input = document.getElementById("tokInput");
  input.value = tok || "";
  document.getElementById("tokStat").textContent = tok ? "active" : "none";
  document.getElementById("tokStat").className = "pstat" + (tok ? " ok" : "");
  document.getElementById("tokGen").textContent = tok ? "Regenerate" : "Generate";
  document.getElementById("tokRevoke").style.display = tok ? "" : "none";
}
async function loadToken(){
  const r = await fetch("/api/auth/token");
  if(!r.ok) return;
  showToken((await r.json()).token);
}
async function genToken(){
  const r = await fetch("/api/auth/token",{method:"POST"});
  if(!r.ok){ toast("Couldn't generate a token."); return; }
  showToken((await r.json()).token); toast("Token generated — copy it now.");
}
async function revokeToken(){
  if(!confirm("Revoke your token? Any agent using it will stop working.")) return;
  await fetch("/api/auth/token",{method:"DELETE"});
  showToken(null); toast("Token revoked.");
}
function copyToken(){
  const v = document.getElementById("tokInput").value;
  if(!v){ toast("Generate a token first."); return; }
  navigator.clipboard?.writeText(v).then(()=>toast("Token copied."),()=>toast("Copy failed — select and copy manually."));
}

/* ---------- timezone + kick-off slot ratings ---------- */
const DEFAULT_SLOTS = [3,2,2,1,1,1,2,2,3,3,3,3,3,3,3,3,4,4,5,5,5,5,5,4];
let SLOTS = DEFAULT_SLOTS.slice();
function slotColor(r){ const s=(Math.max(1,Math.min(5,r))-1)/4; return `hsl(${Math.round(s*125)},72%,${Math.round(81-(1-s)*5)}%)`; }
function browserTz(){ try{ return Intl.DateTimeFormat().resolvedOptions().timeZone||""; }catch(e){ return ""; } }
function tzOptions(){
  try{ return Intl.supportedValuesOf("timeZone"); }
  catch(e){ return [browserTz(),"UTC","Europe/Berlin","Europe/London","America/New_York","America/Los_Angeles","America/Sao_Paulo","Asia/Tokyo","Australia/Sydney"].filter(Boolean); }
}
function fillTzSelect(current){
  const sel=document.getElementById("tzSel");
  const zones=tzOptions().slice();
  if(current && !zones.includes(current)) zones.unshift(current);
  sel.innerHTML=zones.map(z=>`<option value="${z}" ${z===current?"selected":""}>${z}</option>`).join("");
}
function renderSlots(){
  const strip=document.getElementById("slotStrip");
  strip.innerHTML=SLOTS.map((r,h)=>`<button type="button" class="slotcell" data-h="${h}"
    style="background:${slotColor(r)}" title="${String(h).padStart(2,"0")}:00 — ${r}/5">
    <span class="sh">${String(h).padStart(2,"0")}</span><span class="sr">${r}</span></button>`).join("");
  strip.querySelectorAll(".slotcell").forEach(b=>{
    b.onclick=()=>{ const h=+b.dataset.h; SLOTS[h]=SLOTS[h]%5+1; renderSlots(); };
  });
}
async function loadPrefs(){
  const r=await fetch("/api/me/prefs"); if(!r.ok) return;
  const d=await r.json();
  SLOTS=(Array.isArray(d.slots)&&d.slots.length===24)?d.slots.slice():DEFAULT_SLOTS.slice();
  let tz=d.timezone;
  if(!d.tz_explicit){ const bt=browserTz(); if(bt) tz=bt; }
  fillTzSelect(tz); renderSlots();
  // first run: adopt the device's time zone so the schedule matches immediately
  if(!d.tz_explicit){ const bt=browserTz(); if(bt && bt!==d.timezone){
    await fetch("/api/me/prefs",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({timezone:bt})});
    getState();
  }}
}
async function savePrefs(){
  const tz=document.getElementById("tzSel").value;
  const r=await fetch("/api/me/prefs",{method:"PUT",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({timezone:tz,slots:SLOTS})});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){ toast(d.detail||"Couldn't save."); return; }
  toast("Time settings saved."); getState();
}
function detectTz(){ const bt=browserTz(); if(!bt){ toast("Couldn't detect a time zone."); return; }
  fillTzSelect(bt); toast("Set to "+bt+" — click Save to apply."); }
function resetSlots(){ SLOTS=DEFAULT_SLOTS.slice(); renderSlots(); }

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

/* ---------- leaderboard ---------- */
async function loadLeaderboard(){
  const r = await fetch("/api/leaderboard");
  if(!r.ok) return;
  const d = await r.json();
  const s = d.scheme;
  document.getElementById("lbLegend").innerHTML =
    `<span class="lbchip exact">Exact <b>${s.exact}</b></span>
     <span class="lbchip gd">Goal diff <b>${s.goaldiff}</b></span>
     <span class="lbchip tend">Tendency <b>${s.tendency}</b></span>
     <span class="lbmeta">${d.scored_matches} match${d.scored_matches===1?"":"es"} scored</span>`;
  if(!d.standings.length){ document.getElementById("lbTable").innerHTML = `<p class="note">No players yet.</p>`; return; }
  const rows = d.standings.map(p=>`
    <tr class="${p.is_self?'self':''}">
      <td class="rk">${p.rank}</td>
      <td class="pl">${fmtEl(p.username)}${p.is_self?' <span class="youtag">you</span>':''}${p.kind==="teamtip"?' <span class="ttag">teamtip</span>':''}</td>
      <td class="pts">${p.points}</td>
      <td>${p.exact}</td><td>${p.goaldiff}</td><td>${p.tendency}</td><td>${p.miss}</td>
      <td class="tn">${p.tips}</td>
    </tr>`).join("");
  document.getElementById("lbTable").innerHTML = `
    <table class="lbtbl">
      <thead><tr><th>#</th><th>Player</th><th title="Total points">Pts</th>
        <th title="Exact scores">E</th><th title="Correct goal difference">GD</th>
        <th title="Correct tendency">T</th><th title="Missed">✗</th>
        <th title="Tips on played matches">n</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
}

/* ---------- progress charts ---------- */
let PROG_DATA = null;       // {matchdays, series}
let PROG_METRIC = "rank";   // "rank" | "points"
let PROG_HIDDEN = new Set();
const PROG_COLORS = ["#C8102E","#0E7C7B","#1F6FB2","#E0561F","#6A359C","#2E8B57",
  "#A61E4D","#5E7A1E","#2C3E8C","#8B4A2B","#CC6B1F","#A9821B","#3D5A6C","#B3122B"];

/* ---------- by-matchday predictions grid (grouped by round) ---------- */
let MD_LIST = null;        // [{key,label,index,matches,finished}]
let MD_SEL = null;         // selected round key (g1/g2/g3/R32/…)
async function loadMatchdays(){
  const r = await fetch("/api/matchdays");
  if(!r.ok) return;
  MD_LIST = (await r.json()).matchdays || [];
  const sel = document.getElementById("mdSel");
  if(!MD_LIST.length){ sel.innerHTML=""; document.getElementById("mdTable").innerHTML=""; return; }
  // default: latest matchday that has any finished match, else the first
  if(!MD_SEL || !MD_LIST.some(d=>d.key===MD_SEL)){
    const played = [...MD_LIST].reverse().find(d=>d.finished>0);
    MD_SEL = (played || MD_LIST[0]).key;
  }
  sel.innerHTML = MD_LIST.map(d=>{
    const lbl = d.label + (d.finished?` (${d.finished}/${d.matches})`:"");
    return `<option value="${d.key}" ${d.key===MD_SEL?"selected":""}>${fmtEl(lbl)}</option>`;
  }).join("");
  await loadMatchdayGrid();
}
async function loadMatchdayGrid(){
  MD_SEL = document.getElementById("mdSel").value || MD_SEL;
  const r = await fetch(`/api/matchday/${encodeURIComponent(MD_SEL)}`);
  if(!r.ok){ document.getElementById("mdTable").innerHTML = `<p class="note">Couldn't load this matchday.</p>`; return; }
  renderMatchdayGrid(await r.json());
}
function mdCellHTML(cell){
  if(cell===null) return `<td class="mdcell hidden" title="Hidden until kickoff">·</td>`;
  if(!cell.tip)   return `<td class="mdcell none">–</td>`;
  const cls = cell.outcome ? ` ${cell.outcome}` : "";
  const pts = (cell.points!=null) ? `<span class="mdpts">${cell.points}</span>` : "";
  return `<td class="mdcell${cls}"><span class="mdtip">${fmtEl(cell.tip)}</span>${pts}</td>`;
}
function renderMatchdayGrid(d){
  const onlyGhosts = d.rows.every(r=>r.kind==="teamtip");
  document.getElementById("mdEmpty").hidden = d.rows.some(r=>r.kind==="teamtip");
  const meta = document.getElementById("mdMeta");
  const played = d.matches.filter(m=>m.status==="finished").length;
  meta.textContent = `${d.matches.length} match${d.matches.length===1?"":"es"} · ${played} played · ${d.rows.length} player${d.rows.length===1?"":"s"}`;
  if(!d.rows.length){ document.getElementById("mdTable").innerHTML = `<p class="note">No players to show.</p>`; return; }
  // header: player | each match (group · home–away) | MD pts
  const head = `<tr>
    <th class="mdname">Player</th>
    ${d.matches.map(m=>`<th class="mdmatch ${m.revealed?'':'locked'}" title="${fmtEl(m.home)} v ${fmtEl(m.away)} · ${fmtEl(m.kickoff_et)} ET${m.result?` · ${m.result}`:''}">
        ${m.group?`<span class="mg">${fmtEl(m.group)}</span>`:''}
        <span class="mh">${fmtEl(teamAbbr(m.home))}</span><span class="mv">${fmtEl(teamAbbr(m.away))}</span>
        ${m.result?`<span class="mres">${m.result}</span>`:(m.revealed?'':'<span class="mlock">🔒</span>')}
      </th>`).join("")}
    <th class="mdtot">Pts</th>
  </tr>`;
  const body = d.rows.map((row,i)=>`
    <tr class="${row.is_self?'self':''} ${row.kind==='teamtip'?'ghost':''}">
      <td class="mdname"><span class="mdrk">${i+1}</span>${fmtEl(row.name)}${row.kind==='teamtip'?'<span class="ghosttag" title="Imported from teamtip">tt</span>':''}</td>
      ${d.matches.map(m=>mdCellHTML(row.cells[m.id])).join("")}
      <td class="mdtot"><b>${row.matchday_points}</b></td>
    </tr>`).join("");
  const cols = `<colgroup><col class="mdcname">${d.matches.map(()=>`<col class="mdc">`).join("")}<col class="mdctot"></colgroup>`;
  document.getElementById("mdTable").innerHTML =
    `<table class="mdtbl">${cols}<thead>${head}</thead><tbody>${body}</tbody></table>
     <div class="mdkey"><span class="mdcell exact">exact ${d.scheme.exact}</span>
       <span class="mdcell goaldiff">goal diff ${d.scheme.goaldiff}</span>
       <span class="mdcell tendency">tendency ${d.scheme.tendency}</span>
       <span class="mdcell hidden">· hidden until kickoff</span></div>`;
}
// 3-letter code so 24 match columns fit a page without horizontal scroll
const TEAM3 = {"South Korea":"KOR","South Africa":"RSA","Saudi Arabia":"KSA","New Zealand":"NZL",
  "Cape Verde":"CPV","Ivory Coast":"CIV","DR Congo":"COD","Czechia":"CZE","Uzbekistan":"UZB",
  "Bosnia & Herzegovina":"BIH","Netherlands":"NED","Switzerland":"SUI","Australia":"AUS",
  "Argentina":"ARG","Germany":"GER","Portugal":"POR","Morocco":"MAR","Senegal":"SEN",
  "Colombia":"COL","Paraguay":"PAR","Uruguay":"URU","Ecuador":"ECU","Scotland":"SCO",
  "England":"ENG","Croatia":"CRO","Belgium":"BEL","Norway":"NOR","Sweden":"SWE","Austria":"AUT",
  "Tunisia":"TUN","Algeria":"ALG","Egypt":"EGY","Ghana":"GHA","Panama":"PAN","Mexico":"MEX",
  "Canada":"CAN","Brazil":"BRA","France":"FRA","Spain":"ESP","Japan":"JPN","Iran":"IRN",
  "Iraq":"IRQ","Qatar":"QAT","Jordan":"JOR","Haiti":"HAI","Curaçao":"CUW","Türkiye":"TUR","USA":"USA"};
function teamAbbr(name){ return TEAM3[name] || (name||"").slice(0,3).toUpperCase(); }

async function loadProgress(){
  const r = await fetch("/api/progress");
  if(!r.ok) return;
  PROG_DATA = await r.json();
  const empty = !PROG_DATA.matchdays.length;
  document.getElementById("progEmpty").hidden = !empty;
  document.getElementById("progChart").style.display = empty ? "none" : "";
  if(empty){ document.getElementById("progLegend").innerHTML=""; return; }
  renderProgLegend();
  drawProgress();
}

function progColor(i){ return PROG_COLORS[i % PROG_COLORS.length]; }

function renderProgLegend(){
  const leg = document.getElementById("progLegend");
  leg.innerHTML = PROG_DATA.series.map((s,i)=>{
    const off = PROG_HIDDEN.has(s.subject_id) ? " off" : "";
    return `<button class="legitem${off}" data-sid="${fmtEl(s.subject_id)}">
      <span class="swatch" style="background:${progColor(i)}"></span>${fmtEl(s.name)}</button>`;
  }).join("");
  leg.querySelectorAll(".legitem").forEach(b=>{
    b.onclick = ()=>{
      const sid = b.dataset.sid;
      if(PROG_HIDDEN.has(sid)) PROG_HIDDEN.delete(sid); else PROG_HIDDEN.add(sid);
      b.classList.toggle("off");
      drawProgress();
    };
  });
}

function drawProgress(){
  if(!PROG_DATA || !PROG_DATA.matchdays.length) return;
  const cv = document.getElementById("progChart");
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  const padL = 44, padR = 16, padT = 16, padB = 34;
  ctx.clearRect(0,0,W,H);
  const mds = PROG_DATA.matchdays;
  const isRank = PROG_METRIC === "rank";
  // value range
  let vmin = Infinity, vmax = -Infinity;
  PROG_DATA.series.forEach(s=>{
    mds.forEach(md=>{ const v=s[PROG_METRIC][md]; if(v!=null){ vmin=Math.min(vmin,v); vmax=Math.max(vmax,v);} });
  });
  if(!isFinite(vmin)){ return; }
  if(isRank){ vmin=1; vmax=Math.max(vmax, PROG_DATA.series.length); }
  else { vmin=0; vmax=Math.max(vmax,1); }
  const xN = Math.max(mds.length-1, 1);
  const X = i => padL + (W-padL-padR) * (i/xN);
  // rank: smaller is better -> invert so #1 is at the top
  const Y = v => isRank
    ? padT + (H-padT-padB) * ((v-vmin)/(vmax-vmin||1))
    : padT + (H-padT-padB) * (1 - (v-vmin)/(vmax-vmin||1));
  // gridlines + y labels
  ctx.strokeStyle="#e6e0d6"; ctx.fillStyle="#8a8170"; ctx.font="11px system-ui,sans-serif";
  const ticks = 5;
  for(let t=0;t<=ticks;t++){
    const v = vmin + (vmax-vmin)*(t/ticks);
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(W-padR,y); ctx.stroke();
    ctx.fillText(isRank ? Math.round(v) : Math.round(v), 6, y+4);
  }
  // x labels (matchday = # finished)
  ctx.textAlign="center";
  mds.forEach((md,i)=>{ ctx.fillText(md, X(i), H-12); });
  ctx.textAlign="left";
  ctx.fillText("matches played", padL, H-1);
  // lines
  PROG_DATA.series.forEach((s,i)=>{
    if(PROG_HIDDEN.has(s.subject_id)) return;
    ctx.strokeStyle = progColor(i); ctx.lineWidth = s.kind==="user" ? 3 : 1.8;
    ctx.beginPath(); let started=false;
    mds.forEach((md,xi)=>{
      const v = s[PROG_METRIC][md]; if(v==null) return;
      const x=X(xi), y=Y(v);
      if(!started){ ctx.moveTo(x,y); started=true; } else ctx.lineTo(x,y);
    });
    ctx.stroke();
    // end dots
    mds.forEach((md,xi)=>{
      const v=s[PROG_METRIC][md]; if(v==null) return;
      ctx.fillStyle=progColor(i); ctx.beginPath(); ctx.arc(X(xi),Y(v),2.6,0,7); ctx.fill();
    });
  });
}

/* ---------- per-game tips hover popover ---------- */
const TIP_CACHE = {};
let POP_FOR = null;
function popEl(){ return document.getElementById("tipPop"); }
async function tipsFor(id){
  if(!TIP_CACHE[id]) TIP_CACHE[id] = fetch(`/api/match/${id}/tips`).then(r=>r.ok?r.json():null).catch(()=>null);
  return TIP_CACHE[id];
}
function renderPop(d){
  if(!d) return `<div class="poprow muted">Couldn't load tips.</div>`;
  if(!d.revealed) return `<div class="pophead">Everyone's tips</div><div class="poprow muted">Hidden until kickoff</div>`;
  if(!d.tips.length) return `<div class="pophead">Everyone's tips</div><div class="poprow muted">No tips for this match</div>`;
  const rows = d.tips.map(t=>{
    const pts = d.finished ? `<span class="poppts ${t.outcome}">${t.points}</span>` : "";
    return `<div class="poprow ${t.is_self?'self':''}"><span class="popname">${fmtEl(t.username)}</span>
      <span class="popscore">${t.home}–${t.away}</span>${pts}</div>`;
  }).join("");
  return `<div class="pophead">Everyone's tips${d.finished?' · points':''}</div>${rows}`;
}
function positionPop(e){
  const p = popEl(); const pad = 14;
  let x = e.clientX + pad, y = e.clientY + pad;
  const w = p.offsetWidth, h = p.offsetHeight;
  if(x + w > innerWidth - 8) x = e.clientX - w - pad;
  if(y + h > innerHeight - 8) y = innerHeight - h - 8;
  if(y < 8) y = 8;
  p.style.left = x + "px"; p.style.top = y + "px";
}
async function showPop(id, e){
  POP_FOR = id;
  const p = popEl();
  p.innerHTML = `<div class="poprow muted">Loading…</div>`;
  p.hidden = false; positionPop(e);
  const d = await tipsFor(id);
  if(POP_FOR !== id) return;            // moved away while loading
  p.innerHTML = renderPop(d); positionPop(e);
}
function hidePop(){ POP_FOR = null; popEl().hidden = true; }
const CARD_SEL = ".m[data-id],.mini[data-id],.tie[data-id]";
let HOVER_BOUND = false;
function ensureHoverDelegation(){
  if(HOVER_BOUND) return; HOVER_BOUND = true;
  document.body.addEventListener("mouseover", (e)=>{
    const el = e.target.closest(CARD_SEL);
    if(!el) return;
    if(e.target.closest(".predwrap")){ hidePop(); return; }   // prediction shows its own reasoning tooltip
    if(POP_FOR !== el.dataset.id) showPop(el.dataset.id, e);
  });
  document.body.addEventListener("mousemove", (e)=>{ if(POP_FOR) positionPop(e); });
  document.body.addEventListener("mouseout", (e)=>{
    const el = e.target.closest(CARD_SEL);
    if(el && !(e.relatedTarget && el.contains(e.relatedTarget))) hidePop();
  });
}

function toast(msg){
  const t=document.getElementById("toast"); t.textContent=msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t=setTimeout(()=>t.classList.remove("show"),3200);
}

document.querySelectorAll("nav.tabs button").forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll("nav.tabs button").forEach(x=>x.setAttribute("aria-selected", x===b));
    document.querySelectorAll("section.view").forEach(v=>v.classList.toggle("active", v.id===b.dataset.view));
    if(b.dataset.view==="progress") loadProgress();
    if(b.dataset.view==="matchday") loadMatchdays();
  };
});
document.getElementById("mdSel").onchange = loadMatchdayGrid;
document.querySelectorAll("#schedSub button").forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll("#schedSub button").forEach(x=>x.setAttribute("aria-selected", x===b));
    document.getElementById("schedUpcoming").classList.toggle("active", b.dataset.sub==="upcoming");
    document.getElementById("schedPast").classList.toggle("active", b.dataset.sub==="past");
  };
});
document.querySelectorAll("#progSub button").forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll("#progSub button").forEach(x=>x.setAttribute("aria-selected", x===b));
    PROG_METRIC = b.dataset.prog;
    drawProgress();
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
document.getElementById("tokGen").onclick = genToken;
document.getElementById("tokRevoke").onclick = revokeToken;
document.getElementById("tokCopy").onclick = copyToken;
document.getElementById("prefSave").onclick = savePrefs;
document.getElementById("tzDetect").onclick = detectTz;
document.getElementById("slotReset").onclick = resetSlots;
document.getElementById("provSel").onchange = (e)=>{
  ACTIVE_PROVIDER = e.target.value;
  localStorage.setItem("wc_provider", ACTIVE_PROVIDER);
  getState();
};
ensureHoverDelegation();
setAuthMode("login");
checkAuth();
