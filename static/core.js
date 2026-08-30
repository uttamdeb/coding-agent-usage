/* ==========================================================================
   core.js — constants, state, formatting, date ranges, filtering, controls.
   Loaded before charts.js / views.js; everything here is intentionally global.
   ========================================================================== */

/* Tool identity. ORDER is also the categorical-hue order that was validated
   for colour-blind separation (adjacent pairs, light + dark) — see app.css. */
const SRC = {
  claude:           {label:"Claude Code",    v:"--t-claude",          exact:true },
  codex:            {label:"Codex",          v:"--t-codex",           exact:true },
  copilot:          {label:"Copilot",        v:"--t-copilot",         exact:false},
  cursor:           {label:"Cursor",         v:"--t-cursor",          exact:false},
  "claude-desktop": {label:"Claude Desktop", v:"--t-claude-desktop",  exact:true },
  opencode:         {label:"opencode",       v:"--t-opencode",        exact:true },
};
const ORDER = ["claude","codex","copilot","cursor","claude-desktop","opencode"];
const PROVIDERS = ["Anthropic","OpenAI","Google","Other"];
const PROV_VAR = {Anthropic:"--p-anthropic",OpenAI:"--p-openai",Google:"--p-google",Other:"--p-other"};
const DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

/* ---------- css variable access (theme-aware) ---------- */
function cssv(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function srcColor(s){ return cssv((SRC[s]||{}).v || "--text-3"); }
function provColor(p){ return cssv(PROV_VAR[p] || "--p-other"); }
function seqRamp(){ return [0,1,2,3,4,5,6].map(i=>cssv("--seq-"+i)); }
/* A model gets its provider's hue; sibling models are separated by lightness,
   never by an invented hue (identity stays with the provider). */
function modelColor(model){
  const prov = (RAW && RAW.model_vendor && RAW.model_vendor[model]) || "Other";
  const base = provColor(prov);
  const sibs = MODEL_RANK[prov] || [];
  const i = Math.max(0, sibs.indexOf(model));
  return shade(base, i);
}
let MODEL_RANK = {};
function shade(hex, step){
  if(step<=0) return hex;
  const m = hex.replace("#","");
  let r=parseInt(m.slice(0,2),16), g=parseInt(m.slice(2,4),16), b=parseInt(m.slice(4,6),16);
  // alternate lighter / darker so sibling models of one provider stay separable
  // without walking off the end of the lightness band in one direction
  const f = Math.min(0.52, 0.22 + (Math.ceil(step/2)-1)*0.19);
  if(step % 2 === 1){ r+=(255-r)*f; g+=(255-g)*f; b+=(255-b)*f; }
  else             { r*=(1-f);      g*=(1-f);      b*=(1-f); }
  const h=n=>Math.round(n).toString(16).padStart(2,"0");
  return "#"+h(r)+h(g)+h(b);
}

/* ---------- formatting ---------- */
const pad2 = n => (""+n).padStart(2,"0");
const dkey = d => d.getFullYear()+"-"+pad2(d.getMonth()+1)+"-"+pad2(d.getDate());
const fmtTok = n => { n=n||0;
  if(n>=1e9) return (n/1e9).toFixed(2)+"B";
  if(n>=1e6) return (n/1e6).toFixed(1)+"M";
  if(n>=1e3) return (n/1e3).toFixed(1)+"K";
  return ""+Math.round(n); };
const fmtUSD = n => { n=n||0;
  if(n>=1000) return "$"+(n/1000).toFixed(1)+"k";
  if(n>=10)   return "$"+n.toFixed(0);
  return "$"+n.toFixed(2); };
const fmtUSD2 = n => "$"+(n||0).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtNum = n => (n||0).toLocaleString();
const fmtPct = n => (n*100).toFixed(n<0.1?1:0)+"%";
const fmtBytes = n => { n=n||0;
  if(n>=1e9) return (n/1e9).toFixed(2)+" GB";
  if(n>=1e6) return (n/1e6).toFixed(0)+" MB";
  if(n>=1e3) return (n/1e3).toFixed(0)+" KB";
  return n+" B"; };
const esc = s => (s==null?"":(""+s)).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
const recTokens = r => (r.in||0)+(r.out||0)+(r.cr||0)+(r.cc||0);
const ctxTokens = r => (r.in||0)+(r.cr||0)+(r.cc||0);

/* ---------- state ---------- */
let RAW = null, STORAGE = null;
const S = {
  view:"overview",
  preset:"30d", from:null, to:null,
  compare:false,
  tools:new Set(ORDER),
  provs:new Set(),      // empty = all
  projs:new Set(),
  models:new Set(),
  search:"",
  exactOnly:false,
  metric:"tokens", projMetric:"tokens", provMetric:"cost",
  live:true,
  muted:{},             // chartId -> Set of muted series labels
  sessSort:{key:"end",dir:-1}, modelSort:{key:"cost",dir:-1},
  toolSort:{key:"count",dir:-1}, projSort:{key:"tokens",dir:-1},
  fileSort:{key:"bytes",dir:-1},
};

const PRESETS = [
  ["today","Today"],["yesterday","Yesterday"],["7d","Last 7 days"],["14d","Last 14 days"],
  ["30d","Last 30 days"],["90d","Last 90 days"],["180d","Last 180 days"],["365d","Last 365 days"],
  ["wtd","This week"],["mtd","This month"],["qtd","This quarter"],["ytd","This year"],
  ["lastmonth","Last month"],["all","All time"],
];
function presetLabel(p){ const f=PRESETS.find(x=>x[0]===p); return f?f[1]:"Custom"; }

function dataFloor(){
  if(!RAW || !RAW.records.length) return "2000-01-01";
  let lo="9999"; for(const r of RAW.records) if(r.date!=="0000-00-00" && r.date<lo) lo=r.date;
  return lo==="9999"?"2000-01-01":lo;
}
/* Resolve the active window to inclusive local-time YYYY-MM-DD bounds. */
function range(){
  const today = new Date(); today.setHours(0,0,0,0);
  const back = n => { const d=new Date(today); d.setDate(d.getDate()-n); return dkey(d); };
  const p = S.preset;
  if(p==="custom") return {from:S.from||dataFloor(), to:S.to||dkey(today)};
  if(p==="today") return {from:dkey(today), to:dkey(today)};
  if(p==="yesterday") return {from:back(1), to:back(1)};
  if(p==="all") return {from:dataFloor(), to:dkey(today)};
  if(p==="wtd"){ const d=new Date(today); const wd=(d.getDay()+6)%7; d.setDate(d.getDate()-wd); return {from:dkey(d),to:dkey(today)}; }
  if(p==="mtd"){ const d=new Date(today.getFullYear(),today.getMonth(),1); return {from:dkey(d),to:dkey(today)}; }
  if(p==="qtd"){ const d=new Date(today.getFullYear(),Math.floor(today.getMonth()/3)*3,1); return {from:dkey(d),to:dkey(today)}; }
  if(p==="ytd"){ const d=new Date(today.getFullYear(),0,1); return {from:dkey(d),to:dkey(today)}; }
  if(p==="lastmonth"){ const a=new Date(today.getFullYear(),today.getMonth()-1,1),
                             b=new Date(today.getFullYear(),today.getMonth(),0); return {from:dkey(a),to:dkey(b)}; }
  const n = parseInt(p,10)||30;
  return {from:back(n-1), to:dkey(today)};
}
function prevRange(){
  const r = range();
  const a = new Date(r.from+"T00:00:00"), b = new Date(r.to+"T00:00:00");
  const days = Math.round((b-a)/86400000)+1;
  const pb = new Date(a); pb.setDate(pb.getDate()-1);
  const pa = new Date(pb); pa.setDate(pa.getDate()-(days-1));
  return {from:dkey(pa), to:dkey(pb), days};
}
function rangeDays(){ const r=range();
  return Math.round((new Date(r.to+"T00:00:00")-new Date(r.from+"T00:00:00"))/86400000)+1; }
function dateList(r){
  const out=[]; const d=new Date(r.from+"T00:00:00"), end=new Date(r.to+"T00:00:00");
  while(d<=end){ out.push(dkey(d)); d.setDate(d.getDate()+1); if(out.length>1200)break; }
  return out;
}

/* ---------- filtering ---------- */
const providerOf = m => (RAW && RAW.model_vendor && RAW.model_vendor[m]) || "Other";
function passSrc(s){ return S.tools.has(s) && (!S.exactOnly || (SRC[s]||{}).exact); }
function passModel(m){
  if(m==="(user)") return true;                       // user-turn marker rows
  if(S.provs.size && !S.provs.has(providerOf(m))) return false;
  if(S.models.size && !S.models.has(m)) return false;
  return true;
}
function passProj(p){ return !S.projs.size || S.projs.has(p||"(unknown)"); }

function slice(r){
  r = r || range();
  const recs = RAW.records.filter(x => passSrc(x.source) && passModel(x.model)
      && passProj(x.project) && x.date>=r.from && x.date<=r.to);
  const hourly = RAW.hourly.filter(x => passSrc(x.source) && x.date>=r.from && x.date<=r.to);
  const sessions = RAW.sessions.filter(x => passSrc(x.source) && passProj(x.project)
      && passModel(x.model) && (x.end||x.start||"").slice(0,10)>=r.from
      && (x.end||x.start||"").slice(0,10)<=r.to);
  return {recs, hourly, sessions, r};
}
/* Tool-call rows carry a date + source but no project/model dimension, so the
   project / provider / model filters cannot apply to them — the UI says so. */
function toolCounts(r){
  const out = {};
  for(const t of RAW.tools){
    if(!passSrc(t.source)) continue;
    if(t.date < r.from || t.date > r.to) continue;
    const e = out[t.name] || (out[t.name] = {name:t.name, count:0, src:{}});
    e.count += t.count;
    e.src[t.source] = (e.src[t.source]||0) + t.count;
  }
  return Object.values(out).sort((a,b)=>b.count-a.count);
}
function dimFiltered(){ return S.projs.size || S.provs.size || S.models.size; }
function totals(recs){
  const t = {tok:0,cost:0,msgs:0,user:0,tools:0,in:0,out:0,cr:0,cc:0,reason:0,req:0,prem:0,days:new Set()};
  for(const r of recs){
    t.tok+=recTokens(r); t.cost+=r.cost||0; t.msgs+=r.asst||0; t.user+=r.user||0;
    t.tools+=r.tools||0; t.in+=r.in||0; t.out+=r.out||0; t.cr+=r.cr||0; t.cc+=r.cc||0;
    t.reason+=r.reason||0; t.req+=r.req||0; t.prem+=r.prem||0;
    if(recTokens(r)||r.asst||r.user) t.days.add(r.date);
  }
  return t;
}

/* ---------- shared UI helpers ---------- */
/* Interactive legend — ONLY for charts whose render actually honours S.muted.
   A legend that looks clickable but changes nothing is worse than a plain one. */
function legendHTML(id, items){
  return items.map(it=>{
    const off = (S.muted[id]&&S.muted[id].has(it.label))?" off":"";
    return `<span class="li${off}" data-lg="${id}" data-k="${esc(it.label)}">
      <span class="sw" style="background:${it.color}"></span>${esc(it.label)}</span>`;
  }).join("");
}
/* Read-only legend — part-to-whole and normalised charts, where hiding a series
   would misstate the total. */
function legendStatic(items){
  return items.map(it=>`<span class="li static">
      <span class="sw" style="background:${it.color}"></span>${esc(it.label)}</span>`).join("");
}
function isMuted(id,label){ return !!(S.muted[id] && S.muted[id].has(label)); }
function sparkline(vals, color, w, h){
  w=w||64; h=h||20;
  if(!vals.length) return "";
  const max=Math.max(...vals,1), min=Math.min(...vals,0);
  const span=(max-min)||1;
  const pts=vals.map((v,i)=>[i*(w/Math.max(1,vals.length-1)), h-2-((v-min)/span)*(h-4)]);
  const d=pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
  const area=d+` L ${w} ${h} L 0 ${h} Z`;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <path d="${area}" fill="${color}" opacity=".13"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.5"
      stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function deltaHTML(cur, prev, invert){
  if(prev==null || !isFinite(prev) || prev===0) return `<span class="delta flat">—</span>`;
  const d=(cur-prev)/Math.abs(prev);
  if(Math.abs(d)<0.005) return `<span class="delta flat">no change</span>`;
  const up=d>0, good = invert ? !up : up;
  return `<span class="delta ${good?"up":"down"}">${up?"▲":"▼"} ${Math.abs(d*100).toFixed(0)}%</span>`;
}

/* ---------- tooltip ---------- */
const TIP = () => document.getElementById("tip");
function showTip(html, x, y){
  const t=TIP(); t.innerHTML=html; t.classList.add("on");
  const r=t.getBoundingClientRect();
  let left=x+14, top=y+14;
  if(left+r.width>innerWidth-8) left = x-r.width-14;
  if(top+r.height>innerHeight-8) top = y-r.height-14;
  t.style.left=left+"px"; t.style.top=Math.max(8,top)+"px";
}
function hideTip(){ TIP().classList.remove("on"); }

/* ---------- theme ---------- */
function applyTheme(mode){
  const root=document.documentElement;
  if(mode==="auto") root.removeAttribute("data-theme");
  else root.dataset.theme=mode;
  const dark = mode==="dark" || (mode==="auto" &&
    matchMedia("(prefers-color-scheme: dark)").matches);
  root.dataset.resolved = dark?"dark":"light";
  localStorage.setItem("aiu.theme", mode);
  document.getElementById("themeBtn").textContent = mode==="auto"?"◐":(dark?"☾":"☀");
  document.getElementById("themeBtn").title = "Theme: "+mode+" (t)";
  if(RAW) renderAll();
}
function cycleTheme(){
  const cur = localStorage.getItem("aiu.theme")||"auto";
  applyTheme(cur==="auto"?"light":cur==="light"?"dark":"auto");
}

/* ---------- dropdown plumbing ---------- */
function closeDD(except){
  document.querySelectorAll(".dd.open").forEach(d=>{ if(d!==except) d.classList.remove("open"); });
}
document.addEventListener("click", e=>{
  const tog = e.target.closest("[data-dd=toggle]");
  if(tog){ const dd=tog.closest(".dd"); const open=dd.classList.contains("open");
    closeDD(); if(!open) dd.classList.add("open"); e.stopPropagation(); return; }
  if(!e.target.closest(".dd-panel")) closeDD();
});
document.addEventListener("keydown", e=>{
  if(e.key==="Escape"){ closeDD(); closeDrawer(); }
  if(e.target.tagName==="INPUT"||e.target.tagName==="SELECT") return;
  const map={"1":"today","7":"7d","3":"30d","9":"90d","a":"all","m":"mtd"};
  if(map[e.key]){ S.preset=map[e.key]; syncRangeUI(); renderAll(); }
  if(e.key==="t") cycleTheme();
  if(e.key==="r") document.getElementById("refreshBtn").click();
  if(e.key==="/"){ e.preventDefault(); document.getElementById("search").focus(); }
});

/* ---------- drawer ---------- */
function openDrawer(html){
  document.getElementById("drawerBody").innerHTML=html;
  document.getElementById("drawer").classList.add("open");
  document.getElementById("scrim").classList.add("on");
}
function closeDrawer(){
  document.getElementById("drawer").classList.remove("open");
  document.getElementById("scrim").classList.remove("on");
}
