/* ==========================================================================
   views.js — filter controls, the seven views, and the boot sequence.
   ========================================================================== */

/* ---------------- filter bar ---------------- */
function buildRangePanel(){
  const p = document.getElementById("rangePanel");
  const r = range();
  p.innerHTML =
    `<div class="dd-head">Range</div>` +
    PRESETS.map(([k,l])=>`<div class="dd-item" data-preset="${k}">
        <span>${l}</span>${S.preset===k?'<span class="v">●</span>':''}</div>`).join("") +
    `<div class="dd-sep"></div><div class="dd-head">Custom</div>
     <div class="dd-item" style="gap:6px">
       <input type="date" class="field" id="dFrom" value="${r.from}" style="flex:1">
       <span class="dim">→</span>
       <input type="date" class="field" id="dTo" value="${r.to}" style="flex:1">
     </div>
     <div class="dd-item" data-apply="1" style="justify-content:center;color:var(--accent);font-weight:600">Apply custom range</div>`;
}
function syncRangeUI(){
  const r = range();
  const label = S.preset==="custom" ? `${r.from} → ${r.to}` : presetLabel(S.preset);
  document.getElementById("rangeLabel").textContent = label;
  if(!document.getElementById("ddRange").classList.contains("open")) buildRangePanel();
}
/* An empty set means "no filter" for provider/project/model; the tool picker is
   different — it always holds an explicit selection of at least one tool. */
function multiPanel(panelId, labelId, items, set, allLabel, isTools){
  const p = document.getElementById(panelId);
  const on = k => isTools ? set.has(k) : (set.size===0 || set.has(k));
  p.innerHTML = `<div class="dd-head"><span>${allLabel}</span>
      <span><a href="#" data-all="1">${isTools?"all":"clear"}</a>${
        isTools?' · <a href="#" data-none="1">none</a>':""}</span></div>` +
    items.map(it=>`<div class="dd-item" data-k="${esc(it.key)}">
        <input type="checkbox" ${on(it.key)?"checked":""}>
        ${it.color?`<span class="sw" style="background:${it.color}"></span>`:""}
        <span>${esc(it.label)}</span>
        <span class="v">${it.value||""}</span><span class="only" data-only="1">only</span>
      </div>`).join("") || `<div class="dd-item dim">nothing in this range</div>`;
  const n = isTools ? set.size : (set.size || items.length);
  const noun = allLabel.split(" ")[1] || "";
  document.getElementById(labelId).textContent =
    n >= items.length ? allLabel : `${n} of ${items.length} ${noun}`.trim();
}
function buildFilterPanels(){
  const r = range();
  const tokBySrc = {}, tokByProv = {}, tokByProj = {}, tokByModel = {};
  const tokByIde = {};
  for(const x of RAW.records){
    if(x.date<r.from||x.date>r.to) continue;
    const t = recTokens(x);
    tokBySrc[x.source]=(tokBySrc[x.source]||0)+t;
    if(x.model!=="(user)"){
      tokByProv[providerOf(x.model)]=(tokByProv[providerOf(x.model)]||0)+t;
      tokByModel[x.model]=(tokByModel[x.model]||0)+t;
    }
    tokByProj[x.project||"(unknown)"]=(tokByProj[x.project||"(unknown)"]||0)+t;
    tokByIde[x.ide||"(unknown)"]=(tokByIde[x.ide||"(unknown)"]||0)+t;
  }
  multiPanel("idePanel","ideLabel",
    Object.entries(tokByIde).sort((a,b)=>b[1]-a[1])
      .map(([k,v])=>({key:k,label:k,value:fmtTok(v)})),
    S.ides, "All IDEs");
  multiPanel("toolsPanel","toolsLabel",
    ORDER.map(s=>({key:s,label:SRC[s].label,color:srcColor(s),value:fmtTok(tokBySrc[s]||0)})),
    S.tools, "All tools", true);
  multiPanel("provPanel","provLabel",
    PROVIDERS.filter(p=>tokByProv[p]).map(p=>({key:p,label:p,color:provColor(p),value:fmtTok(tokByProv[p])})),
    S.provs, "All providers");
  multiPanel("projPanel","projLabel",
    Object.entries(tokByProj).sort((a,b)=>b[1]-a[1]).slice(0,60)
      .map(([k,v])=>({key:k,label:k,value:fmtTok(v)})),
    S.projs, "All projects");
  multiPanel("modelPanel","modelLabel",
    Object.entries(tokByModel).sort((a,b)=>b[1]-a[1]).slice(0,40)
      .map(([k,v])=>({key:k,label:k,color:modelColor(k),value:fmtTok(v)})),
    S.models, "All models");
}
function renderPills(){
  const out = [];
  if(S.preset!=="30d"){ const r=range();
    out.push(["range", S.preset==="custom"?`${r.from} → ${r.to}`:presetLabel(S.preset)]); }
  if(S.tools.size!==ORDER.length) out.push(["tools", [...S.tools].map(s=>SRC[s].label).join(", ")]);
  S.provs.forEach(p=>out.push(["prov:"+p, p]));
  S.projs.forEach(p=>out.push(["proj:"+p, p]));
  S.models.forEach(m=>out.push(["model:"+m, m]));
  S.ides.forEach(i=>out.push(["ide:"+i, i]));
  if(S.exactOnly) out.push(["exact","exact tokens only"]);
  if(S.search) out.push(["search",`“${S.search}”`]);
  if(S.compare) out.push(["cmp","comparing to previous period"]);
  const el = document.getElementById("pills");
  el.innerHTML = out.length
    ? out.map(([k,l])=>`<span class="pill">${esc(l)}<button data-pill="${esc(k)}" title="remove">×</button></span>`).join("")
      + (out.length>1?`<span class="pill" style="background:none;border-color:var(--border);color:var(--text-3)">
          <button data-pill="__all" style="opacity:1">reset all</button></span>`:"")
    : "";
}

/* ---------------- KPIs ---------------- */
function renderKPIs(d){
  const t = totals(d.recs);
  const prev = S.compare ? totals(slice(prevRange()).recs) : null;
  const prevSess = S.compare ? slice(prevRange()).sessions.length : null;
  const days = dateList(d.r);
  const byDay = {};
  for(const r of d.recs){ const s=byDay[r.date]||(byDay[r.date]={tok:0,cost:0,msgs:0,user:0});
    s.tok+=recTokens(r); s.cost+=r.cost||0; s.msgs+=r.asst||0; s.user+=r.user||0; }
  const series = f => days.map(x=>(byDay[x]||{})[f]||0);
  const bySrc = {};
  for(const r of d.recs){ const s=bySrc[r.source]||(bySrc[r.source]={tok:0,cost:0,msgs:0,user:0});
    s.tok+=recTokens(r); s.cost+=r.cost||0; s.msgs+=r.asst||0;
      // summed across BOTH conventions on purpose: Claude/Codex record prompts on a
      // "(user)" marker row, Copilot/Cursor on the model row. Neither writes both.
      s.user+=r.user||0; }
  const split = f => ORDER.filter(s=>bySrc[s]&&bySrc[s][f])
    .map(s=>`<span style="color:${srcColor(s)}">${f==="cost"?fmtUSD(bySrc[s][f]):fmtTok(bySrc[s][f])}</span>`)
    .join("");
  const acc = cssv("--accent");
  const cards = [
    {lab:"Tokens", val:fmtTok(t.tok), spark:series("tok"), prev:prev&&prev.tok, cur:t.tok, split:split("tok")},
    {lab:"Est. cost", val:fmtUSD(t.cost), spark:series("cost"), prev:prev&&prev.cost, cur:t.cost,
     split:split("cost"), invert:true},
    {lab:"Your prompts", val:fmtNum(t.user), spark:series("user"), prev:prev&&prev.user,
     cur:t.user, split:split("user")},
    {lab:"Assistant msgs", val:fmtNum(t.msgs), spark:series("msgs"), prev:prev&&prev.msgs, cur:t.msgs, split:split("msgs")},
    {lab:"Sessions", val:fmtNum(d.sessions.length), prev:prevSess, cur:d.sessions.length},
    {lab:"Active days", val:fmtNum(t.days.size)+` <span class="dim" style="font-size:13px;font-weight:500">/ ${days.length}</span>`,
     prev:prev&&prev.days.size, cur:t.days.size},
  ];
  document.getElementById("kpis").innerHTML = cards.map(c=>`
    <div class="kpi">
      <div class="lab">${c.lab}</div>
      <div class="val num">${c.val}</div>
      <div class="foot">${S.compare?deltaHTML(c.cur,c.prev,c.invert):""}
        ${c.spark?`<span class="spark">${sparkline(c.spark,acc)}</span>`:""}</div>
      ${c.split?`<div class="split num">${c.split}</div>`:""}
    </div>`).join("");
}

/* ---------------- highlights ---------------- */
function renderHighlights(d){
  const byDay={}, byProj={}, byHour={};
  for(const r of d.recs){ byDay[r.date]=(byDay[r.date]||0)+recTokens(r);
    byProj[r.project]=(byProj[r.project]||0)+(r.cost||0); }
  for(const h of d.hourly) byHour[h.hour]=(byHour[h.hour]||0)+h.tokens;
  const top = o => Object.entries(o).sort((a,b)=>b[1]-a[1])[0];
  const bigDay=top(byDay), topProj=top(byProj), busiest=top(byHour);
  const pricey = d.sessions.slice().sort((a,b)=>(b.cost||0)-(a.cost||0))[0];
  // longest run of consecutive active days inside the range
  const active = new Set(Object.keys(byDay).filter(k=>byDay[k]>0));
  let best=0, run=0, cur=0;
  for(const day of dateList(d.r)){ if(active.has(day)){run++;best=Math.max(best,run);} else run=0; }
  const days=dateList(d.r); for(let i=days.length-1;i>=0;i--){ if(active.has(days[i]))cur++; else break; }
  const items=[
    bigDay&&{l:"Biggest day",v:fmtTok(bigDay[1])+" tokens",s:bigDay[0]},
    pricey&&{l:"Priciest session",v:fmtUSD(pricey.cost),s:(pricey.title||pricey.project||pricey.id)},
    topProj&&{l:"Top project by cost",v:fmtUSD(topProj[1]),s:topProj[0]},
    busiest&&{l:"Busiest hour",v:pad2(busiest[0])+":00",s:fmtTok(busiest[1])+" tokens"},
    {l:"Longest streak",v:best+(best===1?" day":" days"),s:cur?`current streak ${cur}`:"not active today"},
  ].filter(Boolean);
  document.getElementById("highlights").innerHTML = items.map(i=>
    `<div class="item"><div class="l">${i.l}</div><div class="v num" title="${esc(i.s)}">${i.v}</div>
     <div class="s">${esc(i.s)}</div></div>`).join("") || `<div class="empty">Nothing in range.</div>`;
}

/* ---------------- overview charts ---------------- */
function renderDaily(d){
  const days = dateList(d.r);
  const valOf = r => S.metric==="cost"?(r.cost||0) : S.metric==="messages"?(r.asst||0) : recTokens(r);
  const srcs = ORDER.filter(s=>S.tools.has(s));
  const ds = srcs.filter(s=>!isMuted("dailyChart",SRC[s].label)).map(s=>{
    const m={}; for(const r of d.recs) if(r.source===s) m[r.date]=(m[r.date]||0)+valOf(r);
    return stackDS(SRC[s].label, days.map(x=>+(m[x]||0).toFixed(4)), srcColor(s));
  });
  const fmt = S.metric==="cost"?fmtUSD:S.metric==="messages"?fmtNum:fmtTok;
  document.getElementById("dailyLegend").innerHTML =
    legendHTML("dailyChart", srcs.map(s=>({label:SRC[s].label,color:srcColor(s)})));
  document.getElementById("dailyHint").textContent =
    `${S.metric==="cost"?"est. cost":S.metric==="messages"?"assistant messages":"tokens"} per day, stacked by tool`;
  mk("dailyChart",{type:"bar",plugins:[barLabels],
    data:{labels:days.map(shortDay),datasets:ds},
    options:{layout:{padding:{top:18}},
      scales:axes({x:{stacked:true},y:{stacked:true,ticks:{callback:v=>fmt(v)}}}),
      plugins:{barLabels:{fmt},tooltip:{callbacks:{
        label:c=>" "+c.dataset.label+": "+fmt(c.parsed.y),
        footer:it=>"total "+fmt(it.reduce((a,x)=>a+x.parsed.y,0))}}}}});
}
const shortDay = d => d.slice(5).replace("-","/");

function renderToolShare(d){
  const valOf = r => S.metric==="cost"?(r.cost||0) : S.metric==="messages"?(r.asst||0) : recTokens(r);
  const m={}; for(const r of d.recs) m[r.source]=(m[r.source]||0)+valOf(r);
  const rows = ORDER.filter(s=>m[s]).map(s=>({label:SRC[s].label,value:m[s],color:srcColor(s)}))
    .sort((a,b)=>b.value-a.value);
  shareBars(document.getElementById("toolShare"), rows,
    S.metric==="cost"?fmtUSD:S.metric==="messages"?fmtNum:fmtTok);
}
function renderHeat(d){
  const cells = Array.from({length:7},()=>Array(24).fill(0));
  let max=0;
  for(const h of d.hourly){
    const dow=(new Date(h.date+"T00:00:00").getDay()+6)%7;
    cells[dow][h.hour]+=h.tokens; max=Math.max(max,cells[dow][h.hour]);
  }
  renderHeatmap(cells,max);
  document.getElementById("hmHint").textContent =
    "local hour × weekday, by tokens" + (dimFiltered()?" · tool + date filters only":"");
}
function renderComposition(d){
  const KINDS=[["in","Input","--k-in"],["cr","Cache read","--k-cr"],
               ["cc","Cache write","--k-cw"],["out","Output","--k-out"]];
  const srcs = ORDER.filter(s=>S.tools.has(s));
  const agg={}; let reason=0;
  for(const r of d.recs){ const a=agg[r.source]||(agg[r.source]={in:0,cr:0,cc:0,out:0});
    a.in+=r.in||0; a.cr+=r.cr||0; a.cc+=r.cc||0; a.out+=r.out||0; reason+=r.reason||0; }
  const labels = srcs.filter(s=>agg[s]);
  const ds = KINDS.filter(k=>!isMuted("compChart",k[1])).map(([k,lab,v])=>({
    label:lab, data:labels.map(s=>agg[s][k]), backgroundColor:cssv(v), stack:"a",
    borderColor:cssv("--surface"), borderWidth:{left:2}, borderRadius:3,
    borderSkipped:false, maxBarThickness:26}));
  document.getElementById("compLegend").innerHTML =
    legendHTML("compChart", KINDS.map(([k,lab,v])=>({label:lab,color:cssv(v)})))
    + (reason?`<span class="li dim" title="Reasoning tokens are already inside Output — shown here, not stacked, to avoid double counting">of which reasoning: ${fmtTok(reason)}</span>`:"");
  mk("compChart",{type:"bar",
    data:{labels:labels.map(s=>SRC[s].label),datasets:ds},
    options:{indexAxis:"y",
      scales:axes({x:{stacked:true,grid:{display:true,color:cssv("--grid")},ticks:{callback:v=>fmtTok(v)}},
                   y:{stacked:true,grid:{display:false},ticks:{color:cssv("--text-2")}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmtTok(c.parsed.x),
        footer:it=>"total "+fmtTok(it.reduce((a,x)=>a+x.parsed.x,0))}}}}});
}

function viewOverview(d){
  renderKPIs(d); renderHighlights(d);
  const byDay={}; let max=0;
  for(const r of RAW.records){
    if(!passSrc(r.source)||!passModel(r.model)||!passProj(r.project)) continue;
    byDay[r.date]=(byDay[r.date]||0)+recTokens(r); max=Math.max(max,byDay[r.date]);
  }
  renderCalendar(byDay, max, day=>{ S.preset="custom"; S.from=day; S.to=day; syncRangeUI(); renderAll(); });
  renderDaily(d); renderToolShare(d); renderHeat(d); renderComposition(d);
}

/* ---------------- helpers shared by the analytic views ---------------- */
function priceOf(m){ return (RAW.pricing && RAW.pricing[m]) || [0,0,0,0,0]; }
function metricOf(kind){
  return kind==="cost" ? (r=>r.cost||0)
       : kind==="messages" ? (r=>r.asst||0)
       : (r=>recTokens(r));
}
function fmtOf(kind){ return kind==="cost"?fmtUSD:kind==="messages"?fmtNum:fmtTok; }
function sortRows(rows, st){
  return rows.slice().sort((a,b)=>{
    let av=a[st.key], bv=b[st.key];
    if(typeof av==="string"||typeof bv==="string")
      return st.dir*((""+(av==null?"":av)).localeCompare(""+(bv==null?"":bv)));
    return st.dir*((av||0)-(bv||0));
  });
}
const TEXTCOL=new Set(["model","source","project","name","prov","cat","server","tool",
  "path","last","when","label","title","branch","entry"]);
function thead(cols, st, tag){
  return "<thead><tr>"+cols.map(([k,l,title])=>
    `<th data-k="${k}" data-t="${tag}" class="${TEXTCOL.has(k)?"":"r"}"${
      title?` title="${esc(title)}"`:""}>${l}${
      st.key===k?(st.dir<0?" ↓":" ↑"):""}</th>`).join("")+"</tr></thead>";
}
function srcBadge(s){
  const c = srcColor(s);
  return `<span class="badge" style="color:${c};border-color:${c}44;background:${c}1f">${SRC[s].label}</span>`;
}
function domSource(srcMap){
  let best=null,bv=-1; for(const k in srcMap) if(srcMap[k]>bv){bv=srcMap[k];best=k;}
  return best;
}

/* ---------------- COST ---------------- */
function viewCost(d){
  const t = totals(d.recs);
  let saved=0;
  for(const r of d.recs){ const p=priceOf(r.model); saved += (r.cr||0)*(p[0]-p[4])/1e6; }
  const days = Math.max(1, t.days.size);
  const perDay = t.cost/days;
  const ctx = t.in+t.cr+t.cc;
  const stats = [
    {l:"Total est. cost", v:fmtUSD2(t.cost), s:`${rangeDays()} day window`},
    {l:"Per active day", v:fmtUSD(perDay), s:`${days} active of ${rangeDays()}`},
    {l:"30-day run rate", v:fmtUSD(perDay*30), s:"at the current pace"},
    {l:"Per session", v:fmtUSD(d.sessions.length?t.cost/d.sessions.length:0), s:`${fmtNum(d.sessions.length)} sessions`},
    {l:"Per prompt", v:fmtUSD(t.user?t.cost/t.user:0), s:`${fmtNum(t.user)} prompts`},
    {l:"Cache hit rate", v:ctx?fmtPct(t.cr/ctx):"—", s:`${fmtTok(t.cr)} read from cache`},
    {l:"Saved by caching", v:fmtUSD(saved), s:"vs. re-sending that context"},
    {l:"Blended rate", v:t.tok?"$"+(t.cost/t.tok*1e6).toFixed(2):"—", s:"per 1M tokens"},
    {l:"Output share", v:t.tok?fmtPct(t.out/t.tok):"—", s:`${fmtTok(t.out)} generated`},
  ];
  document.getElementById("costStats").innerHTML = stats.map(x=>
    `<div class="stat"><div class="l">${x.l}</div><div class="v num">${x.v}</div><div class="s">${x.s}</div></div>`).join("");

  // cumulative cost by tool
  const days2 = dateList(d.r);
  const srcs = ORDER.filter(s=>S.tools.has(s));
  const ds = srcs.filter(s=>!isMuted("cumChart",SRC[s].label)).map(s=>{
    const m={}; for(const r of d.recs) if(r.source===s) m[r.date]=(m[r.date]||0)+(r.cost||0);
    let run=0;
    return areaDS(SRC[s].label, days2.map(x=>+(run+=(m[x]||0)).toFixed(2)), srcColor(s));
  });
  if(S.compare){
    const pd = slice(prevRange());
    const m={}; for(const r of pd.recs) m[r.date]=(m[r.date]||0)+(r.cost||0);
    const pdays = dateList(prevRange()); let run=0, prevData;
    ds.push({label:"Previous period (all tools)",
      data:(prevData=pdays.map(x=>+(run+=(m[x]||0)).toFixed(2)).slice(0,days2.length)),
      borderColor:cssv("--text-3"), borderDash:[5,4], borderWidth:1.5,
      pointRadius:soloPoint(prevData), fill:false, tension:.25, stack:"b"});
  }
  document.getElementById("cumLegend").innerHTML =
    legendHTML("cumChart", srcs.map(s=>({label:SRC[s].label,color:srcColor(s)})));
  mk("cumChart",{type:"line",data:{labels:days2.map(shortDay),datasets:ds},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({y:{stacked:true,ticks:{callback:v=>fmtUSD(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmtUSD2(c.parsed.y)}}}}});

  // cost by project (cost by *tool* is already the Overview's share-by-tool card)
  const byProj={}, projSrc={};
  for(const r of d.recs){
    if(!r.cost) continue;
    byProj[r.project]=(byProj[r.project]||0)+r.cost;
    (projSrc[r.project]=projSrc[r.project]||{})[r.source]=
      (projSrc[r.project][r.source]||0)+r.cost;
  }
  hbar("costTool", Object.entries(byProj).sort((a,b)=>b[1]-a[1]).slice(0,12)
        .map(([p,v])=>({label:p,value:v,color:srcColor(domSource(projSrc[p]))})), {fmt:fmtUSD});

  // cache hit rate over time (one series, one axis)
  const cd={}; for(const r of d.recs){ const c=cd[r.date]||(cd[r.date]={cr:0,ctx:0});
    c.cr+=r.cr||0; c.ctx+=ctxTokens(r); }
  let cacheData;
  mk("cacheChart",{type:"line",
    data:{labels:days2.map(shortDay),datasets:[{
      label:"Cache hit rate",
      data:(cacheData=days2.map(x=>cd[x]&&cd[x].ctx?+(cd[x].cr/cd[x].ctx*100).toFixed(1):null)),
      borderColor:cssv("--accent"), backgroundColor:cssv("--accent")+"22",
      borderWidth:2, pointRadius:soloPoint(cacheData), pointHoverRadius:4,
      tension:.3, fill:true, spanGaps:true}]},
    options:{scales:axes({y:{min:0,max:100,ticks:{callback:v=>v+"%"}}}),
      plugins:{tooltip:{callbacks:{label:c=>" cache hit "+c.parsed.y+"%"}}}}});

  // blended rate by model.
  //   "all" = cost / every token, cache reads included. Correct, but the divisor is
  //     ~95% cache reads, so the bar mostly tracks how well-cached a model was and is
  //     NOT comparable across providers (OpenAI bills no cache writes at all).
  //   "out" = cost / output tokens — the whole cost over the work actually generated,
  //     which is comparable across models and providers.
  const perOut = S.rateMetric==="out";
  const bm={};
  for(const r of d.recs){ if(r.model==="(user)") continue;
    const b=bm[r.model]||(bm[r.model]={tok:0,out:0,cost:0});
    b.tok+=recTokens(r); b.out+=r.out||0; b.cost+=r.cost||0; }
  const rows=Object.entries(bm)
    .filter(([,v])=>perOut ? v.out>1000 : v.tok>10000)
    .map(([m,v])=>({label:m,value:v.cost/(perOut?v.out:v.tok)*1e6,color:modelColor(m)}))
    .sort((a,b)=>b.value-a.value).slice(0,12);
  hbar("rateChart", rows, {fmt:v=>"$"+(v>=100?Math.round(v):v.toFixed(2))});
  document.getElementById("rateHint").innerHTML = perOut
    ? "total cost &divide; output tokens &mdash; comparable across models and providers"
    : "a <em>rate</em>, not a total &mdash; multiply by a model's tokens to get its cost";

  // daily cost stacked
  const dsc = srcs.map(s=>{
    const m={}; for(const r of d.recs) if(r.source===s) m[r.date]=(m[r.date]||0)+(r.cost||0);
    return stackDS(SRC[s].label, days2.map(x=>+(m[x]||0).toFixed(3)), srcColor(s));
  });
  mk("dailyCost",{type:"bar",plugins:[barLabels],
    data:{labels:days2.map(shortDay),datasets:dsc},
    options:{layout:{padding:{top:18}},
      scales:axes({x:{stacked:true},y:{stacked:true,ticks:{callback:v=>fmtUSD(v)}}}),
      plugins:{barLabels:{fmt:fmtUSD2},tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmtUSD2(c.parsed.y),
        footer:it=>"total "+fmtUSD2(it.reduce((a,x)=>a+x.parsed.y,0))}}}}});
}

/* ---------------- MODELS & PROVIDERS ---------------- */
function viewModels(d){
  const val = metricOf(S.provMetric), fmt = fmtOf(S.provMetric);
  const recs = d.recs.filter(r=>r.model!=="(user)");
  const prov={}, provModels={}, provTools={}, mt={};
  for(const r of recs){
    const p=providerOf(r.model), v=val(r);
    prov[p]=(prov[p]||0)+v;
    (provModels[p]=provModels[p]||{})[r.model]=(provModels[p][r.model]||0)+v;
    (provTools[p]=provTools[p]||{})[r.source]=(provTools[p][r.source]||0)+v;
    const k=r.model+"\t"+r.source;
    const e=mt[k]||(mt[k]={model:r.model,source:r.source,tok:0,cost:0,msgs:0,in:0,out:0,cr:0,cc:0});
    e.tok+=recTokens(r); e.cost+=r.cost||0; e.msgs+=r.asst||0;
    e.in+=r.in||0; e.out+=r.out||0; e.cr+=r.cr||0; e.cc+=r.cc||0;
  }
  const provs = PROVIDERS.filter(p=>prov[p]);
  const grand = provs.reduce((a,p)=>a+prov[p],0)||1;

  // head-to-head
  document.getElementById("provH2H").innerHTML = provs.map(p=>{
    const models=Object.entries(provModels[p]).sort((a,b)=>b[1]-a[1]);
    const tools=Object.entries(provTools[p]).sort((a,b)=>b[1]-a[1]);
    return `<div class="item" style="flex:1 1 220px;background:var(--surface-2);border-radius:var(--radius-sm);padding:13px 15px;margin-bottom:0">
      <div style="display:flex;align-items:center;gap:8px">
        <span class="swatch" style="background:${provColor(p)};margin:0"></span>
        <strong>${p}</strong><span class="dim num" style="margin-left:auto">${fmtPct(prov[p]/grand)}</span></div>
      <div class="num" style="font-size:22px;font-weight:660;margin:6px 0 4px">${fmt(prov[p])}</div>
      <div class="meter" style="margin-bottom:8px"><i style="width:${prov[p]/grand*100}%;background:${provColor(p)}"></i></div>
      <div class="s dim" style="font-size:11.5px">${models.length} model${models.length>1?"s":""} · top ${esc(models[0][0])}</div>
      <div class="s dim" style="font-size:11.5px">ran in ${tools.map(([s])=>SRC[s].label).join(", ")}</div>
    </div>`;
  }).join("") || `<div class="empty">Nothing in range.</div>`;
  document.getElementById("provHint").textContent =
    "who made the model — independent of which tool you ran it in" +
    (S.provMetric==="tokens" ? " · Copilot/Cursor token counts are estimates, prefer Cost or Msgs" : "");

  // concentric doughnut: provider (inner) → model (outer)
  const outer=[], outerC=[], outerL=[], outerM=[];
  provs.forEach(p=>Object.entries(provModels[p]).sort((a,b)=>b[1]-a[1])
    .filter(([,v])=>v>0)                       // $0 / 0-token models aren't slices
    .forEach(([m,v])=>{
      outer.push(+v.toFixed(4)); outerC.push(modelColor(m));
      outerL.push(m+" · "+p); outerM.push(m); }));
  const rings = [];
  if(provs.length > 2)     // with 1–2 providers the ring only restates the cards above
    rings.push({data:provs.map(p=>+prov[p].toFixed(4)), backgroundColor:provs.map(provColor),
                _labels:provs, weight:.5, borderColor:cssv("--surface"), borderWidth:2});
  rings.push({data:outer, backgroundColor:outerC, _labels:outerL, weight:1,
              borderColor:cssv("--surface"), borderWidth:2});
  mk("provDonut",{type:"doughnut",
    data:{labels:outerL, datasets:rings},
    options:{cutout:"46%",plugins:{tooltip:{itemSort:null,callbacks:{
      label:c=>" "+c.dataset._labels[c.dataIndex]+": "+fmt(c.parsed)}}}}});

  const donutHint = document.querySelector("#v-models .hint[data-donut]");
  if(donutHint) donutHint.textContent = rings.length>1
    ? "inner ring provider · outer ring model" : "models, coloured by provider";
  document.getElementById("provDonutLegend").innerHTML = legendStatic(
    rings.length>1 ? provs.map(p=>({label:p,color:provColor(p)}))
                   : outerM.slice(0,10).map(m=>({label:m,color:modelColor(m)})));

  // provider share over time (100%)
  const days = dateList(d.r);
  const per={}; for(const r of recs){ const p=providerOf(r.model);
    (per[r.date]=per[r.date]||{})[p]=(per[r.date][p]||0)+val(r); }
  const shareDS = provs.map(p=>{
    const data=days.map(x=>{ const row=per[x]; if(!row) return null;
      const tot=Object.values(row).reduce((a,b)=>a+b,0); return tot?+(100*(row[p]||0)/tot).toFixed(1):null; });
    return {label:p,data,borderColor:provColor(p),backgroundColor:provColor(p)+"44",
      borderWidth:1.5,pointRadius:soloPoint(data),pointHoverRadius:4,tension:.25,
      fill:true,stack:"a",spanGaps:false};
  });
  // normalised to 100% — muting a provider would make the rest not add up
  document.getElementById("provShareLegend").innerHTML =
    legendStatic(provs.map(p=>({label:p,color:provColor(p)})));
  mk("provShare",{type:"line",data:{labels:days.map(shortDay),datasets:shareDS},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({y:{stacked:true,min:0,max:100,ticks:{callback:v=>v+"%"}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+(c.parsed.y||0)+"%"}}}}});

  // model timeline
  const mtot={}; for(const r of recs) mtot[r.model]=(mtot[r.model]||0)+val(r);
  const topM = Object.entries(mtot).filter(([,v])=>v>0)
    .sort((a,b)=>b[1]-a[1]).slice(0,8).map(x=>x[0]);
  const byDM={}; for(const r of recs){ if(!topM.includes(r.model)) continue;
    (byDM[r.model]=byDM[r.model]||{})[r.date]=(byDM[r.model][r.date]||0)+val(r); }
  const tlDS = topM.filter(m=>!isMuted("modelTL",m)).map(m=>
    areaDS(m, days.map(x=>+((byDM[m]||{})[x]||0).toFixed(4)), modelColor(m)));
  document.getElementById("modelTLLegend").innerHTML =
    legendHTML("modelTL", topM.map(m=>({label:m,color:modelColor(m)})));
  mk("modelTL",{type:"line",data:{labels:days.map(shortDay),datasets:tlDS},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({y:{stacked:true,ticks:{callback:v=>fmt(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmt(c.parsed.y)}}}}});

  // provider x tool matrix
  const srcs = ORDER.filter(s=>S.tools.has(s) && recs.some(r=>r.source===s));
  let h = `<thead><tr><th>Provider</th>${srcs.map(s=>`<th class="r">${SRC[s].label}</th>`).join("")}<th class="r">Total</th></tr></thead><tbody>`;
  for(const p of provs){
    h += `<tr><td><span class="swatch" style="background:${provColor(p)}"></span>${p}</td>` +
      srcs.map(s=>`<td class="num r">${(provTools[p]||{})[s]?fmt(provTools[p][s]):'<span class="dim">—</span>'}</td>`).join("") +
      `<td class="num r"><strong>${fmt(prov[p])}</strong></td></tr>`;
  }
  document.getElementById("provMatrix").innerHTML = h+"</tbody>";

  // model x tool table
  const tokGrand = Object.values(mt).reduce((a,x)=>a+x.tok,0) || 1;
  const rows = Object.values(mt).filter(e=>e.tok||e.cost||e.msgs).map(e=>({...e,
    share:e.tok/tokGrand,
    rate:e.tok?e.cost/e.tok*1e6:0, prov:providerOf(e.model)}));
  const cols=[["model","Model"],["source","Tool"],["prov","Provider"],["tok","Tokens"],
    ["out","Output"],["cr","Cache read"],["cost","Est. $"],["msgs","Msgs"],
    ["rate","$/Mtok","effective blended rate"],["share","Share"]];
  const sorted = sortRows(rows, S.modelSort);
  document.getElementById("modelTable").innerHTML = thead(cols,S.modelSort,"model")+"<tbody>"+
    sorted.map(r=>`<tr>
      <td><span class="swatch" style="background:${modelColor(r.model)}"></span>${esc(r.model)}
        ${priceOf(r.model)[0]===0?'<span class="dim" title="no price row in PRICING — cost reads as $0">⚠</span>':''}</td>
      <td>${srcBadge(r.source)}</td><td class="dim">${r.prov}</td>
      <td class="num r">${fmtTok(r.tok)}</td><td class="num r">${fmtTok(r.out)}</td>
      <td class="num r">${fmtTok(r.cr)}</td><td class="num r">${fmtUSD(r.cost)}</td>
      <td class="num r">${fmtNum(r.msgs)}</td><td class="num r">$${r.rate.toFixed(2)}</td>
      <td class="num r">${fmtPct(r.share)}</td></tr>`).join("")+"</tbody>";
}

/* ---------------- TOOLS & AGENTS ---------------- */
const CATS=[
  ["Read / search", /^(read|glob|grep|ls|list_dir|read_file|file_search|semantic_search|codebase_search|grep_search|search|list_code_usages|toolsearch|view_image|view)/i],
  ["Edit / write",  /^(edit|write|multiedit|notebookedit|apply_patch|str_replace|create_file|insert_edit|replace_string|create_directory|patch)/i],
  ["Execute",       /^(bash|shell|exec|wait|run_in_terminal|run_command|execute|terminal|python|get_terminal|kill)/i],
  ["Web",           /^(webfetch|websearch|web_search|web_fetch|fetch|fetch_webpage|open_simple_browser)/i],
  ["Agents / tasks",/^(task|agent|workflow|sendmessage|todowrite|update_plan|manage_todo|think|skill|askuserquestion|exitplanmode|enterplanmode)/i],
];
function categorize(name){
  if(/^mcp__|^mcp_/i.test(name)) return "MCP";
  for(const [lab,re] of CATS) if(re.test(name)) return lab;
  return "Other";
}
function viewTools(d){

  // ---- IDE x tool matrix ------------------------------------------------
  // The IDE is a real dimension now, so show it rather than only offering it as
  // a filter. Copilot's is derived from which editor's storage the log came from;
  // Claude/Codex stamp an entrypoint. Rows are IDEs, columns the tools run in them.
  {
    const val = metricOf(S.ideMetric), fmt = fmtOf(S.ideMetric);
    const byIde = {}, ideTot = {}, srcTot = {};
    for(const r of d.recs){
      const i = r.ide || "(unknown)", v = val(r);
      if(!v) continue;
      (byIde[i] = byIde[i] || {})[r.source] = (byIde[i][r.source] || 0) + v;
      ideTot[i] = (ideTot[i] || 0) + v;
      srcTot[r.source] = (srcTot[r.source] || 0) + v;
    }
    const ides = Object.keys(ideTot).sort((a,b)=>ideTot[b]-ideTot[a]);
    const srcs = ORDER.filter(x => srcTot[x]);
    const grand = ides.reduce((a,i)=>a+ideTot[i],0) || 1;
    let h = `<table><thead><tr><th>IDE / surface</th>${
      srcs.map(x=>`<th class="r">${SRC[x].label}</th>`).join("")
    }<th class="r">Total</th><th class="r">Share</th></tr></thead><tbody>`;
    for(const i of ides){
      h += `<tr><td>${esc(i)}</td>` + srcs.map(x =>
        `<td class="num r">${byIde[i][x] ? fmt(byIde[i][x]) : '<span class="dim">—</span>'}</td>`
      ).join("") + `<td class="num r"><strong>${fmt(ideTot[i])}</strong></td>`
        + `<td class="num r dim">${fmtPct(ideTot[i]/grand)}</td></tr>`;
    }
    document.getElementById("ideMatrix").innerHTML = h + "</tbody></table>";
    document.getElementById("ideHint").textContent = ides.length > 1
      ? `${ides.length} surfaces in range · a VS Code extension logs "VS Code" whatever fork hosts it`
      : "which IDE / surface each tool ran in";
  }
  const tools = toolCounts(d.r);
  const t = totals(d.recs);
  const totalCalls = tools.reduce((a,x)=>a+x.count,0);
  const side = d.sessions.reduce((a,s)=>a+(s.side||0),0);
  const mcp = tools.filter(x=>categorize(x.name)==="MCP");
  const web = tools.filter(x=>categorize(x.name)==="Web").reduce((a,x)=>a+x.count,0);
  const stats=[
    {l:"Tool calls",v:fmtNum(totalCalls),s:`${tools.length} distinct tools`},
    {l:"Per prompt",v:t.user?(totalCalls/t.user).toFixed(1):"—",s:`${fmtNum(t.user)} prompts`},
    {l:"Per assistant msg",v:t.msgs?(totalCalls/t.msgs).toFixed(2):"—",s:`${fmtNum(t.msgs)} messages`},
    {l:"Tokens per prompt",v:t.user?fmtTok(t.tok/t.user):"—",s:"context amplification"},
    {l:"Msgs per session",v:d.sessions.length?(t.msgs/d.sessions.length).toFixed(1):"—",s:`${fmtNum(d.sessions.length)} sessions`},
    {l:"Subagent tokens",v:t.tok?fmtPct(side/t.tok):"—",s:`${fmtTok(side)} in spawned agents`},
    {l:"MCP calls",v:fmtNum(mcp.reduce((a,x)=>a+x.count,0)),s:`${mcp.length} MCP tools`},
    {l:"Web lookups",v:fmtNum(web),s:"search / fetch calls"},
  ];
  // Copilot bills in premium requests, not tokens — its one exact usage number
  const prem = d.recs.reduce((a,r)=>a+(r.prem||0),0);
  if(prem) stats.push({l:"Premium requests",v:fmtNum(Math.round(prem)),
    s:"Copilot's billing unit"});
  // Cursor is the only tool that records how much of its output you kept
  const al=(RAW.ai_lines||[]).filter(x=>x.date>=d.r.from&&x.date<=d.r.to);
  if(al.length){
    const acc=al.reduce((a,x)=>a+x.tab_accepted+x.composer_accepted,0);
    const sug=al.reduce((a,x)=>a+x.tab_suggested+x.composer_suggested,0);
    stats.push({l:"AI lines kept",v:fmtNum(acc),
      s:sug?`${fmtPct(acc/sug)} of ${fmtNum(sug)} suggested · Cursor`:"Cursor"});
  }
  document.getElementById("agentStats").innerHTML = stats.map(x=>
    `<div class="stat"><div class="l">${x.l}</div><div class="v num">${x.v}</div><div class="s">${x.s}</div></div>`).join("");

  hbar("toolChart", tools.slice(0,18).map(x=>({label:x.name,value:x.count,
    color:srcColor(domSource(x.src))})), {fmt:fmtNum, thick:13,
    sub:r=>""});

  const cat={}; for(const x of tools) cat[categorize(x.name)]=(cat[categorize(x.name)]||0)+x.count;
  const catOrder=["Read / search","Edit / write","Execute","Web","Agents / tasks","MCP","Other"]
    .filter(c=>cat[c]);
  const catCols=["--k-in","--k-cr","--k-cw","--k-out","--k-think","--p-google","--text-3"];
  const cmap={}; catOrder.forEach((c,i)=>cmap[c]=cssv(catCols[i%catCols.length]));
  mk("catChart",{type:"doughnut",
    data:{labels:catOrder,datasets:[{data:catOrder.map(c=>cat[c]),
      backgroundColor:catOrder.map(c=>cmap[c]),borderColor:cssv("--surface"),borderWidth:2}]},
    options:{cutout:"58%",plugins:{tooltip:{itemSort:null,callbacks:{
      label:c=>" "+c.label+": "+fmtNum(c.parsed)+" ("+fmtPct(c.parsed/totalCalls)+")"}}}}});
  document.getElementById("catLegend").innerHTML =
    legendStatic(catOrder.map(c=>({label:c,color:cmap[c]})));

  const mcpRows = mcp.map(x=>{
    const parts=x.name.replace(/^mcp__/,"").split("__");
    return {server:parts[0]||"?",tool:parts.slice(1).join("__")||x.name,count:x.count};
  });
  const byServer={}; for(const r of mcpRows){ const e=byServer[r.server]||(byServer[r.server]={server:r.server,count:0,tools:0});
    e.count+=r.count; e.tools++; }
  const srv=Object.values(byServer).sort((a,b)=>b.count-a.count);
  document.getElementById("mcpTable").innerHTML = srv.length
    ? `<thead><tr><th>Server</th><th class="r">Tools used</th><th class="r">Calls</th></tr></thead><tbody>`+
      srv.map(r=>`<tr><td>${esc(r.server)}</td><td class="num r">${r.tools}</td>
        <td class="num r">${fmtNum(r.count)}</td></tr>`).join("")+"</tbody>"
    : `<tbody><tr><td class="empty">No MCP tool calls in range.</td></tr></tbody>`;

  const trows = tools.map(x=>({name:x.name,count:x.count,cat:categorize(x.name),
    source:SRC[domSource(x.src)].label}));
  const tcols=[["name","Tool"],["cat","Category"],["source","Mostly from"],["count","Calls"]];
  document.getElementById("toolTable").innerHTML = thead(tcols,S.toolSort,"tool")+"<tbody>"+
    sortRows(trows,S.toolSort).map(r=>`<tr><td class="name">${esc(r.name)}</td>
      <td class="dim">${r.cat}</td><td class="dim">${r.source}</td>
      <td class="num r">${fmtNum(r.count)}</td></tr>`).join("")+"</tbody>";
}

/* ---------------- PROJECTS ---------------- */
function viewProjects(d){
  const val = metricOf(S.projMetric), fmt = fmtOf(S.projMetric);
  const by={};
  for(const r of d.recs){
    const e=by[r.project]||(by[r.project]={project:r.project,tokens:0,cost:0,messages:0,
      prompts:0,tools:0,src:{}});
    e.tokens+=recTokens(r); e.cost+=r.cost||0; e.messages+=r.asst||0;
    e.prompts+=r.user||0; e.tools+=r.tools||0;
    e.src[r.source]=(e.src[r.source]||0)+recTokens(r);
  }
  const sess={}; for(const s of d.sessions) sess[s.project]=(sess[s.project]||0)+1;
  const q=S.search.toLowerCase();
  const rows=Object.values(by)
    .filter(e=>!q||(e.project||"").toLowerCase().includes(q))
    .map(e=>({...e,sessions:sess[e.project]||0,
    value:S.projMetric==="cost"?e.cost:S.projMetric==="messages"?e.messages:e.tokens}));
  const top=rows.slice().sort((a,b)=>b.value-a.value);
  hbar("projChart", top.slice(0,16).map(r=>({label:r.project,value:r.value,
    color:srcColor(domSource(r.src))})), {fmt});

  const grand=top.reduce((a,r)=>a+r.value,0)||1;
  const top3=top.slice(0,3).reduce((a,r)=>a+r.value,0);
  document.getElementById("projStats").innerHTML=[
    {l:"Projects",v:fmtNum(rows.length),s:"with activity in range"},
    {l:"Top project",v:fmtPct((top[0]||{value:0}).value/grand),s:(top[0]||{}).project||"—"},
    {l:"Top 3 share",v:fmtPct(top3/grand),s:"concentration of effort"},
    {l:"Per project",v:fmt(grand/(rows.length||1)),s:"average"},
  ].map(x=>`<div class="stat" style="flex:1 1 120px"><div class="l">${x.l}</div>
      <div class="v num">${x.v}</div><div class="s">${esc(x.s)}</div></div>`).join("");

  const cols=[["project","Project"],["tokens","Tokens"],["cost","Est. $"],["messages","Msgs"],
    ["prompts","Prompts"],["tools","Tool calls"],["sessions","Sessions"]];
  const maxTok=Math.max(...rows.map(r=>r.tokens),1);
  document.getElementById("projTable").innerHTML=thead(cols,S.projSort,"proj")+"<tbody>"+
    sortRows(rows,S.projSort).map(r=>`<tr class="clickable" data-proj="${esc(r.project)}">
      <td class="name bar-cell"><span class="fill" style="width:${r.tokens/maxTok*100}%"></span>
        <span><span class="swatch" style="background:${srcColor(domSource(r.src))}"></span>${esc(r.project)}</span></td>
      <td class="num r">${fmtTok(r.tokens)}</td><td class="num r">${fmtUSD(r.cost)}</td>
      <td class="num r">${fmtNum(r.messages)}</td><td class="num r">${fmtNum(r.prompts)}</td>
      <td class="num r">${fmtNum(r.tools)}</td><td class="num r">${fmtNum(r.sessions)}</td></tr>`).join("")+"</tbody>";
}

/* ---------------- SESSIONS ---------------- */
function sessionRows(d){
  const q=S.search.toLowerCase();
  return d.sessions.map(s=>({...s,
    tok:(s.in||0)+(s.out||0)+(s.cr||0)+(s.cc||0),
    when:s.end||s.start||"",
    cache:((s.in||0)+(s.cr||0)+(s.cc||0))?(s.cr||0)/((s.in||0)+(s.cr||0)+(s.cc||0)):0,
    name:s.title||s.project||s.id,
  })).filter(s=>!q || (s.name+" "+(s.project||"")+" "+(s.model||"")+" "+(s.branch||"")).toLowerCase().includes(q));
}
function viewSessions(d){
  const rows=sessionRows(d);
  const cols=[["when","When"],["source","Tool"],["name","Session"],["project","Project"],
    ["model","Model"],["tok","Tokens"],["cost","Est. $"],["user","Prompts"],["asst","Msgs"],
    ["tools","Tools"],["cache","Cache %"]];
  const sorted=sortRows(rows,S.sessSort).slice(0,400);
  document.getElementById("sessTable").innerHTML=thead(cols,S.sessSort,"sess")+"<tbody>"+
    sorted.map((s,i)=>`<tr class="clickable" data-sess="${i}">
      <td class="dim">${s.when?s.when.slice(0,16).replace("T"," "):"—"}</td>
      <td>${srcBadge(s.source)}</td>
      <td class="name" title="${esc(s.title||"")}">${
        s.clipped?`<span class="dim" style="margin-right:5px" title="This session also ran outside the selected range — the figures shown cover only its activity inside it (it spans ${s.span} days).">◔</span>`:""}${
        s.subagent?`<span class="sub-badge" title="A subagent transcript — work this session's parent delegated to a Task agent.">sub</span>`:""}${esc(s.name)}</td>
      <td class="dim">${esc(s.project||"—")}</td>
      <td>${esc(s.model)}${s.nmodels>1?` <span class="dim" title="${esc((s.models||[]).join(" · "))}">+${s.nmodels-1}</span>`:""}</td>
      <td class="num r">${fmtTok(s.tok)}</td><td class="num r">${fmtUSD(s.cost)}</td>
      <td class="num r">${fmtNum(s.user)}</td><td class="num r">${fmtNum(s.asst||s.req)}</td>
      <td class="num r">${fmtNum(s.tools)}</td>
      <td class="num r">${s.cache?fmtPct(s.cache):'<span class="dim">—</span>'}</td></tr>`).join("")+"</tbody>";
  const capped = RAW.sessions_total && RAW.sessions_total > RAW.sessions.length;
  const clipped = rows.filter(x=>x.clipped).length;
  document.getElementById("sessHint").textContent =
    `${fmtNum(rows.length)} sessions active in range`
    + (clipped?` · ${clipped} also ran outside it (◔ = figures cover the range only)`:"")
    + (rows.length>400?" · showing the top 400 by the current sort":"")
    + (capped?` · of the ${fmtNum(RAW.sessions_total)} most recent loaded`:"")
    + " · click a row for detail";
  SESS_CACHE = sorted;
}
let SESS_CACHE=[];
function openSession(i){
  const s=SESS_CACHE[i]; if(!s) return;
  const row=(k,v)=>v==null||v===""?"":`<div class="r" style="display:flex;justify-content:space-between;gap:14px;padding:5px 0;border-bottom:1px solid var(--border)">
      <span class="dim">${k}</span><span class="num" style="text-align:right">${v}</span></div>`;
  openDrawer(`
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--text-3);font-weight:640">Session</div>
    <h2 style="margin:4px 0 2px;font-size:17px;font-weight:650">${esc(s.title||s.project||s.id)}</h2>
    <div style="margin-bottom:14px">${srcBadge(s.source)} <span class="dim">${esc(s.id)}</span></div>
    ${row("Project",esc(s.project||"—"))}
    ${row("Model",esc((s.models||[s.model]).join(" · ")))}
    ${row("Git branch",s.branch?esc(s.branch):null)}
    ${row("Entrypoint",s.entry?esc(s.entry):null)}
    ${row("Tool version",s.cliver?esc(s.cliver):null)}
    ${s.clipped?`<div class="warnbar" style="margin:10px 0">Figures below cover the
       selected range only — this session spans ${s.span} days in total.</div>`:""}
    ${row("Started",s.start?s.start.slice(0,19).replace("T"," "):"—")}
    ${row("Last activity",s.end?s.end.slice(0,19).replace("T"," "):"—")}
    ${row("Est. cost",fmtUSD2(s.cost))}
    ${row("Tokens",fmtNum(s.tok))}
    ${row("· input",fmtNum(s.in))}
    ${row("· cache read",fmtNum(s.cr))}
    ${row("· cache write",fmtNum(s.cc))}
    ${row("· output",fmtNum(s.out))}
    ${row("Cache hit rate",s.cache?fmtPct(s.cache):"—")}
    ${row("Your prompts",fmtNum(s.user))}
    ${row("Assistant msgs",fmtNum(s.asst||s.req))}
    ${row("Tool calls",fmtNum(s.tools))}
    ${row("Mode",s.mode?esc(s.mode):null)}
    ${row("Premium requests",s.prem?fmtNum(Math.round(s.prem)):null)}
    ${row("Lines added",s.lines_add?fmtNum(s.lines_add):null)}
    ${row("Lines removed",s.lines_del?fmtNum(s.lines_del):null)}
    ${row("Thinking time",s.think_ms?Math.round(s.think_ms/1000)+"s":null)}
    ${row("Subagents",s.subagents?fmtNum(s.subagents):null)}
    ${row("Subagent tokens",s.side?fmtNum(s.side):null)}
    ${row("Log size",s.bytes?fmtBytes(s.bytes):null)}
    ${s.archived?'<div class="warnbar" style="margin-top:12px">This log has been pruned from disk; its numbers are retained from an earlier scan.</div>':""}
  `);
}

/* ---------------- SETTINGS ---------------- */
async function openSettings(){
  openDrawer('<div class="empty">Loading…</div>');
  let cfg; try{ cfg=await (await fetch("/api/settings")).json(); }
  catch(e){ openDrawer('<div class="empty">Could not read settings.</div>'); return; }
  renderSettings(cfg);
}
/* Offline app (PWA) — strictly opt-in. A service worker keeps controlling this
   origin until it is unregistered, and localhost ports get reused by other tools,
   so nothing is registered unless the user turns it on here. */
const PWA_KEY = "aiu.pwa";
function pwaWanted(){ try{ return localStorage.getItem(PWA_KEY)==="1"; }catch(e){ return false; } }
function pwaSupported(){ return "serviceWorker" in navigator; }
async function pwaEnable(){
  try{ localStorage.setItem(PWA_KEY,"1"); }catch(e){}
  await navigator.serviceWorker.register("/sw.js");
}
async function pwaDisable(){
  try{ localStorage.removeItem(PWA_KEY); }catch(e){}
  const regs = await navigator.serviceWorker.getRegistrations();
  await Promise.all(regs.map(r=>r.unregister()));
  if(window.caches){
    const keys = await caches.keys();
    await Promise.all(keys.filter(k=>k.startsWith("ai-usage-")).map(k=>caches.delete(k)));
  }
}

/* Refresh interval, in ms. 15s is the historical default; 0 = manual only. */
const POLL_KEY="aiu.poll";
const POLL_CHOICES=[["15000","15s"],["60000","1m"],["300000","5m"],["0","Manual"]];
function pollMs(){
  try{ const v=localStorage.getItem(POLL_KEY); return v===null?15000:Math.max(0,+v||0); }
  catch(e){ return 15000; }
}
function setPollMs(v){
  try{ localStorage.setItem(POLL_KEY,String(v)); }catch(e){}
  applyPollInterval();
}

function renderSettings(cfg){
  const days=cfg.claude_cleanup_days, def=cfg.claude_cleanup_default;
  openDrawer(`
    <div class="stg-eyebrow">Settings</div>
    <h2 class="stg-h">Claude Code log retention</h2>
    <div class="stg-note">Claude Code deletes its own session transcripts after this
      many days &mdash; the <code>cleanupPeriodDays</code> setting. Codex, by contrast,
      keeps everything forever. Raise it to keep more history on disk.</div>
    <div class="stg-note">Shortening it never shrinks your analytics: this dashboard
      keeps every session it has already parsed, even after the tool deletes the log.</div>
    <div class="stg-field">
      <input class="field" type="number" id="stgDays" min="1" max="36500" step="1"
        placeholder="${def}" value="${days==null?"":days}" aria-label="Retention in days">
      <span class="stg-hint">days<br>blank = tool default (${def})</span>
    </div>
    <div class="stg-actions">
      <button class="btn primary" id="stgSave">Save</button>
      <button class="btn" id="stgReset">Reset to default</button>
    </div>
    <div class="stg-msg" id="stgMsg"></div>
    <div class="stg-path"><b>Writes to</b><span>${esc(cfg.claude_settings_path)}</span>${
      cfg.claude_settings_exists?"":"<br>Not created yet &mdash; it will be on first save."}
      <br>Other settings in the file are preserved, and a <span>.bak</span> is kept.</div>

    <h2 class="stg-h" style="margin-top:26px">Install as an app</h2>
    <div class="stg-note">Off by default. Turning this on registers a service worker so
      the dashboard can be installed to your Dock or taskbar and still open its shell when
      <code>dashboard.py</code> isn't running. Your usage data is never cached &mdash;
      <code>/api/</code> always goes to the live server.</div>
    <div class="stg-note">A service worker keeps controlling <code>${esc(location.host)}</code>
      until you turn it off here, including for any <em>other</em> tool you later run on this
      port. Turning it off unregisters it and clears its cache.</div>
    <div class="stg-actions">
      <button class="btn${pwaWanted()?"":" primary"}" id="pwaBtn"${pwaSupported()?"":" disabled"}>${
        pwaWanted()?"Turn off":"Turn on"}</button>
      <span class="stg-hint" id="pwaState" style="align-self:center">${
        !pwaSupported() ? "Not available in this browser"
        : pwaWanted() ? "On \u2014 installable, shell cached" : "Off"}</span>
    </div>
    <div class="stg-msg" id="pwaMsg"></div>

    <h2 class="stg-h" style="margin-top:26px">Refresh interval</h2>
    <div class="stg-note">How often the page re-fetches <code>/api/data</code> (about
      ${fmtBytes(cfg.cache_bytes||0)} of JSON each time). Slower saves CPU and disk churn;
      <b>Manual</b> updates only when you press <b>&#8635;</b>. The server keeps parsing
      either way &mdash; this is just how often the browser asks.</div>
    <div class="seg" id="pollSeg" style="margin-top:14px">${
      POLL_CHOICES.map(([v,l])=>`<button data-ms="${v}"${
        String(pollMs())===v?' class="on"':''}>${l}</button>`).join("")}</div>

    <h2 class="stg-h" style="margin-top:26px">Analytics cache</h2>
    <div class="stg-note">This dashboard's own parsed data &mdash;
      <b>${fmtBytes(cfg.cache_bytes||0)}</b> across ${fmtNum(cfg.cache_files||0)} files:
      your prompts, project names and costs. <b>Rebuild</b> re-reads every log from
      scratch. <b>Delete</b> removes the file now, but the next refresh writes it again
      from whatever logs are still on disk &mdash; to keep it gone, stop
      <code>dashboard.py</code> first.</div>
    <div class="stg-note" style="border-left-color:var(--bad)">Both discard the
      <b>durable ledger</b>: sessions whose logs a tool has already deleted exist only in
      this cache, and nothing can bring them back. Your totals will drop by whatever those
      sessions contributed.</div>
    <div class="stg-actions">
      <button class="btn" id="cacheRebuild">Rebuild now</button>
      <button class="btn" id="cacheDelete">Delete</button>
    </div>
    <div class="stg-msg" id="cacheMsg"></div>
    <div class="stg-path"><b>Cache file</b><span>${esc(cfg.cache_path||"")}</span></div>
  `);
  const msgEl=document.getElementById("stgMsg");
  const msg=(t,cls)=>{ msgEl.textContent=t; msgEl.className="stg-msg"+(cls?" "+cls:""); };
  const send=async(value,okText,btn)=>{
    const btns=[...document.querySelectorAll(".stg-actions .btn")];
    btns.forEach(b=>b.disabled=true); msg("Saving\u2026");
    try{
      const r=await fetch("/api/settings",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({cleanupPeriodDays:value})});
      const out=await r.json();
      if(!r.ok) throw new Error(out.error||"request failed");
      renderSettings(out); msg(okText,"ok");
    }catch(e){ btns.forEach(b=>b.disabled=false); msg(e.message,"err"); }
  };
  document.getElementById("stgSave").addEventListener("click",()=>{
    const raw=document.getElementById("stgDays").value.trim();
    if(raw===""){ send(null,"Cleared \u2014 Claude Code's default now applies."); return; }
    const v=Number(raw);
    if(!Number.isInteger(v)||v<1||v>36500){
      msg("Enter a whole number of days between 1 and 36500, or leave it blank.","err"); return; }
    send(v,`Saved \u2014 transcripts now kept for ${v} day${v===1?"":"s"}.`);
  });
  document.getElementById("stgReset").addEventListener("click",()=>
    send(null,"Reset \u2014 Claude Code's default now applies."));

  const seg=document.getElementById("pollSeg");
  if(seg) seg.addEventListener("click",e=>{
    const b=e.target.closest("button[data-ms]"); if(!b) return;
    setPollMs(+b.dataset.ms);
    [...seg.children].forEach(x=>x.classList.toggle("on",x===b));
  });

  const cacheMsg=()=>document.getElementById("cacheMsg");
  const cacheDo=async(action,confirmText)=>{
    const btns=[document.getElementById("cacheRebuild"),document.getElementById("cacheDelete")];
    if(!confirm(confirmText)) return;
    btns.forEach(b=>b.disabled=true);
    cacheMsg().className="stg-msg"; cacheMsg().textContent=
      action==="rebuild"?"Re-reading every log\u2026 this can take a minute.":"Deleting\u2026";
    try{
      const r=await fetch("/api/cache",{method:"POST",
        headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
      const out=await r.json();
      if(!r.ok) throw new Error(out.error||"request failed");
      await load(); await loadStorage();
      const cfg2=await (await fetch("/api/settings")).json();
      renderSettings(cfg2);
      const m=document.getElementById("cacheMsg"); m.className="stg-msg ok";
      m.textContent = out.action==="rebuild"
        ? `Rebuilt ${fmtNum(out.files)} files in ${out.seconds}s.`
        : `Deleted. ${fmtNum(out.dropped)} parsed files dropped from memory.`;
    }catch(e){ btns.forEach(b=>b.disabled=false);
      cacheMsg().className="stg-msg err"; cacheMsg().textContent=e.message; }
  };
  document.getElementById("cacheRebuild").addEventListener("click",()=>cacheDo("rebuild",
    "Rebuild the analytics cache?\n\nEvery log is re-read from scratch. Sessions whose logs "
    +"a tool has already deleted cannot be recovered and will disappear from your totals."));
  document.getElementById("cacheDelete").addEventListener("click",()=>cacheDo("delete",
    "Delete the analytics cache?\n\nThe file is removed now, but a running server "
    +"rewrites it on the next refresh from logs still on disk. What does NOT come back: "
    +"sessions whose logs a tool already deleted."));

  const pwaBtn=document.getElementById("pwaBtn");
  if(pwaBtn && pwaSupported()) pwaBtn.addEventListener("click",async()=>{
    const turningOn=!pwaWanted(); const m=document.getElementById("pwaMsg");
    pwaBtn.disabled=true; m.className="stg-msg"; m.textContent=turningOn?"Registering\u2026":"Removing\u2026";
    try{
      if(turningOn){ await pwaEnable(); renderSettings(cfg);
        document.getElementById("pwaMsg").className="stg-msg ok";
        document.getElementById("pwaMsg").textContent=
          "On. Use your browser's Install / Add to Dock to place it alongside your apps.";
      }else{ await pwaDisable(); renderSettings(cfg);
        document.getElementById("pwaMsg").className="stg-msg ok";
        document.getElementById("pwaMsg").textContent=
          "Off. Service worker unregistered and its cache cleared.";
      }
    }catch(e){ pwaBtn.disabled=false; m.className="stg-msg err"; m.textContent=e.message; }
  });
}

/* ---------------- STORAGE ---------------- */
function viewStorage(){
  if(!STORAGE){ document.getElementById("stStats").innerHTML='<div class="empty">Measuring…</div>'; return; }
  const st=STORAGE;
  const total=st.sources.reduce((a,s)=>a+s.bytes,0);
  const files=st.sources.reduce((a,s)=>a+s.files,0);
  const extras=st.extras.reduce((a,e)=>a+e.bytes,0);
  const disk=st.disk||{total:0,free:0,used:0};
  const biggest=st.files[0];
  document.getElementById("stStats").innerHTML=[
    {l:"Log data on disk",v:fmtBytes(total),s:`${fmtNum(files)} files across ${st.sources.length} tools`},
    {l:"Largest single log",v:biggest?fmtBytes(biggest.bytes):"—",s:biggest?SRC[biggest.source].label+" · "+biggest.project:""},
    {l:"Related, not analysed",v:fmtBytes(extras),s:"see the panel below"},
    {l:"Share of drive",v:disk.total?fmtPct(total/disk.total):"—",s:`of ${fmtBytes(disk.total)}`},
    {l:"Dashboard cache",v:fmtBytes(st.cache_bytes),s:".usage_cache.json"},
  ].map(x=>`<div class="stat" style="flex:1 1 130px"><div class="l">${x.l}</div>
      <div class="v num">${x.v}</div><div class="s">${esc(x.s)}</div></div>`).join("");

  const otherUsed=Math.max(0,(disk.used||0)-total-extras);
  document.getElementById("diskMeter").innerHTML=`
    <div class="meter" style="height:14px">
      <i style="width:${disk.total?total/disk.total*100:0}%;background:var(--accent)" title="AI logs"></i>
      <i style="width:${disk.total?extras/disk.total*100:0}%;background:var(--warn)" title="related AI data"></i>
      <i style="width:${disk.total?otherUsed/disk.total*100:0}%;background:var(--surface-3)" title="everything else"></i>
    </div>
    <div class="legend" style="margin-top:8px">
      <span class="li"><span class="sw" style="background:var(--accent)"></span>AI logs ${fmtBytes(total)}</span>
      <span class="li"><span class="sw" style="background:var(--warn)"></span>related AI data ${fmtBytes(extras)}</span>
      <span class="li"><span class="sw" style="background:var(--surface-3)"></span>everything else ${fmtBytes(otherUsed)}</span>
      <span class="li dim">free ${fmtBytes(disk.free)}</span>
    </div>
    ${disk.total && disk.free/disk.total < 0.1 ? `<div class="warnbar bad" style="margin-top:12px">
      <span>⚠</span><div><strong>Only ${fmtBytes(disk.free)} free (${fmtPct(disk.free/disk.total)}).</strong>
      These logs alone are ${(total/disk.free).toFixed(1)}× your remaining headroom — the cleanup
      commands on the right reclaim the oldest of them without losing any analytics.</div></div>`:""}`;

  hbar("stByTool", st.sources.filter(s=>s.bytes).map(s=>({
    label:(SRC[s.source]||{label:s.source}).label, value:s.bytes, color:srcColor(s.source)})),
    {fmt:fmtBytes});

  // bytes per 1M tokens — how expensively each tool stores its history
  const tokBySrc={};
  for(const r of RAW.records) tokBySrc[r.source]=(tokBySrc[r.source]||0)+recTokens(r);
  hbar("stEff", st.sources.filter(s=>s.bytes&&tokBySrc[s.source]>1e6).map(s=>({
    label:(SRC[s.source]||{label:s.source}).label,
    value:s.bytes/(tokBySrc[s.source]/1e6), color:srcColor(s.source)})).sort((a,b)=>b.value-a.value),
    {fmt:fmtBytes});

  // cumulative accumulation
  const days=[...new Set(st.growth.map(g=>g.date))].filter(x=>x&&x!=="unknown").sort();
  const srcs=[...new Set(st.growth.map(g=>g.source))]
    .sort((a,b)=>ORDER.indexOf(a)-ORDER.indexOf(b));
  const ds=srcs.filter(s=>!isMuted("stGrowth",(SRC[s]||{label:s}).label)).map(s=>{
    const m={};
    for(const g of st.growth) if(g.source===s) m[g.date]=(m[g.date]||0)+g.bytes;
    let run=0; return areaDS((SRC[s]||{label:s}).label, days.map(x=>(run+=(m[x]||0))), srcColor(s));
  });
  document.getElementById("stGrowthLegend").innerHTML=
    legendHTML("stGrowth", srcs.map(s=>({label:(SRC[s]||{label:s}).label,color:srcColor(s)})));
  mk("stGrowth",{type:"line",data:{labels:days.map(shortDay),datasets:ds},
    options:{interaction:{mode:"index",intersect:false},
      scales:axes({y:{stacked:true,ticks:{callback:v=>fmtBytes(v)}}}),
      plugins:{tooltip:{callbacks:{label:c=>" "+c.dataset.label+": "+fmtBytes(c.parsed.y)}}}}});

  const cols=[["bytes","Size"],["source","Tool"],["project","Project"],["last","Last written"],["path","Path"]];
  const rows=sortRows(st.files,S.fileSort).slice(0,120);   // sort all, then show 120
  document.getElementById("stFiles").innerHTML=thead(cols,S.fileSort,"file")+"<tbody>"+
    rows.map(f=>`<tr><td class="num r"><strong>${fmtBytes(f.bytes)}</strong></td>
      <td>${srcBadge(f.source)}</td><td class="dim">${esc(f.project)}</td>
      <td class="dim">${(f.last||"").slice(0,10)||"—"}</td>
      <td class="name dim" title="${esc(f.path)}">…/${esc(shortPath(f.path))}</td></tr>`).join("")+"</tbody>";
  const nTotal=st.files_total||st.files.length;
  document.getElementById("stBigHint").textContent =
    `showing ${Math.min(120,rows.length)} of ${fmtNum(nTotal)} live log files`
    + (nTotal>st.files.length?` · sortable over the largest ${fmtNum(st.files.length)}`:"");

  document.getElementById("stExtras").innerHTML=
    `<thead><tr><th>What</th><th class="r">Size</th></tr></thead><tbody>`+
    st.extras.map(e=>`<tr><td class="name" title="${esc(e.path)}">${esc(e.label)}
      ${e.note?`<div class="dim" style="font-size:11px;white-space:normal">${esc(e.note)}</div>`:""}</td>
      <td class="num r">${fmtBytes(e.bytes)}</td></tr>`).join("")+"</tbody>";

  // reclaimable + the commands are computed server-side, over every live file and
  // for the shell this machine actually runs
  const plan = st.cleanup || {commands:[], days:90, shell:""};
  const rc = (st.reclaimable||{})[String(plan.days)] || {files:0, bytes:0};
  document.getElementById("cleanup").innerHTML=`
    <div class="hint" style="margin:14px 0 4px">Reclaim space — the dashboard keeps every session it has
      already parsed, so deleting old logs does <strong>not</strong> shrink your analytics.
      ${rc.files?`<br><strong>${fmtBytes(rc.bytes)}</strong> sits in ${fmtNum(rc.files)} log(s)
        untouched for ${plan.days}+ days.`:""}</div>
    ${plan.commands.length?`<div class="hint">Run in <strong>${esc(plan.shell)}</strong> — paths are
      this machine's:</div><code class="cmd">${plan.commands.map(esc).join("\n")}</code>`:""}
    <div class="hint" style="margin-top:8px">Claude Code prunes on its own after
      <code>cleanupPeriodDays</code> (default 30) in its <code>settings.json</code>; Codex keeps
      everything forever.</div>`;
}
// last two path components, on either separator (logs may come from either OS)
function shortPath(p){ return String(p||"").split(/[\\/]/).filter(Boolean).slice(-2).join("/"); }

/* ---------------- dispatch ---------------- */
const VIEW_HTML={};
function renderAll(){
  if(!RAW) return;
  // model colour ranking: siblings of a provider separate by lightness
  const tot={};
  for(const r of RAW.records){ if(r.model==="(user)") continue;
    tot[r.model]=(tot[r.model]||0)+recTokens(r); }
  MODEL_RANK={};
  Object.entries(tot).sort((a,b)=>b[1]-a[1]).forEach(([m])=>{
    const p=providerOf(m); (MODEL_RANK[p]=MODEL_RANK[p]||[]).push(m); });

  syncRangeUI(); buildFilterPanels(); renderPills();
  document.querySelectorAll(".view").forEach(v=>v.classList.toggle("on", v.id==="v-"+S.view));
  document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on", b.dataset.v===S.view));
  const d = slice();
  const host = document.getElementById("v-"+S.view);
  if(!VIEW_HTML[S.view]) VIEW_HTML[S.view] = host.innerHTML;
  const empty = !d.recs.length && !d.sessions.length;
  if(empty && S.view!=="storage"){
    host.innerHTML = `<div class="grid"><div class="card col-12"><div class="empty">
      No activity for this range and filter combination.</div></div></div>`;
    host.dataset.emptied = "1";
    return;
  }
  if(host.dataset.emptied==="1"){ host.innerHTML = VIEW_HTML[S.view]; host.dataset.emptied="0"; }
  try{
    if(S.view==="overview") viewOverview(d);
    else if(S.view==="cost") viewCost(d);
    else if(S.view==="models") viewModels(d);
    else if(S.view==="tools") viewTools(d);
    else if(S.view==="projects") viewProjects(d);
    else if(S.view==="sessions") viewSessions(d);
    else if(S.view==="optimize") viewOptimize(d);
    else if(S.view==="storage") viewStorage();
  }catch(err){
    console.error("render failed", err);
    document.documentElement.dataset.jsError = "render "+S.view+": "+(err&&err.message||err);
  }
}

/* ---------------- data ---------------- */
async function load(){
  try{
    const r=await fetch("/api/data"); RAW=await r.json();
    const dates=RAW.records.map(x=>x.date).filter(x=>x!=="0000-00-00");
    const lo=dates.length?dates.reduce((a,b)=>a<b?a:b):"—";
    const hi=dates.length?dates.reduce((a,b)=>a>b?a:b):"—";
    const fresh=new Date(RAW.meta.last_refresh*1000).toLocaleTimeString();
    document.getElementById("subtitle").textContent =
      `${fmtNum(RAW.meta.files)} log files · ${lo} → ${hi} · refreshed ${fresh}`;
    document.getElementById("pricingNote").textContent=RAW.pricing_note;
    renderAll();
  }catch(e){
    document.getElementById("subtitle").innerHTML=
      '<span style="color:var(--bad)">⚠ cannot reach /api/data — is dashboard.py running?</span>';
  }
}
async function loadStorage(){
  try{ const r=await fetch("/api/storage"); STORAGE=await r.json();
    if(S.view==="storage") renderAll(); }catch(e){}
}

/* ---------------- events ---------------- */
document.getElementById("tabs").addEventListener("click",e=>{
  const b=e.target.closest("button[data-v]"); if(!b) return;
  S.view=b.dataset.v; location.hash=S.view;
  if(S.view==="storage" && !STORAGE) loadStorage();
  renderAll();
});
document.getElementById("rangePanel").addEventListener("click",e=>{
  const it=e.target.closest("[data-preset]");
  if(it){ S.preset=it.dataset.preset; closeDD(); renderAll(); return; }
  if(e.target.closest("[data-apply]")){
    const a=document.getElementById("dFrom").value, b=document.getElementById("dTo").value;
    if(a&&b){ S.preset="custom"; S.from=a<b?a:b; S.to=a<b?b:a; closeDD(); renderAll(); }
  }
});
document.getElementById("cmpSeg").addEventListener("click",e=>{
  const b=e.target.closest("button"); if(!b) return;
  S.compare=b.dataset.c==="1";
  [...e.currentTarget.children].forEach(x=>x.classList.toggle("on",x===b)); renderAll();
});
function wireMulti(panelId, get, isTools){
  document.getElementById(panelId).addEventListener("click",e=>{
    const set=get();
    if(e.target.closest("[data-all]")){ e.preventDefault();
      if(isTools) ORDER.forEach(s=>set.add(s)); else set.clear(); renderAll(); return; }
    if(e.target.closest("[data-none]")){ e.preventDefault();
      if(isTools){ set.clear(); set.add(ORDER[0]); }
      renderAll(); return; }
    const it=e.target.closest(".dd-item[data-k]"); if(!it) return;
    const k=it.dataset.k;
    if(e.target.closest("[data-only]")){ set.clear(); set.add(k); renderAll(); return; }
    if(isTools){ if(set.has(k)){ if(set.size>1) set.delete(k); } else set.add(k); }
    else { if(set.has(k)) set.delete(k); else set.add(k); }
    renderAll();
  });
}
wireMulti("toolsPanel",()=>S.tools,true);
wireMulti("provPanel",()=>S.provs,false);
wireMulti("projPanel",()=>S.projs,false);
wireMulti("idePanel",()=>S.ides,false);
wireMulti("modelPanel",()=>S.models,false);

document.getElementById("pills").addEventListener("click",e=>{
  const b=e.target.closest("[data-pill]"); if(!b) return;
  const k=b.dataset.pill;
  if(k==="__all"){ S.preset="30d"; S.tools=new Set(ORDER); S.provs.clear(); S.projs.clear();
    S.models.clear(); S.search=""; S.exactOnly=false; S.compare=false;
    document.getElementById("search").value="";
    document.querySelectorAll("#cmpSeg button").forEach((x,i)=>x.classList.toggle("on",i===0));
    document.getElementById("reliableBtn").classList.remove("on"); }
  else if(k==="range") S.preset="30d";
  else if(k==="tools") S.tools=new Set(ORDER);
  else if(k==="exact"){ S.exactOnly=false; document.getElementById("reliableBtn").classList.remove("on"); }
  else if(k==="search"){ S.search=""; document.getElementById("search").value=""; }
  else if(k==="cmp"){ S.compare=false;
    document.querySelectorAll("#cmpSeg button").forEach((x,i)=>x.classList.toggle("on",i===0)); }
  else if(k.startsWith("prov:")) S.provs.delete(k.slice(5));
  else if(k.startsWith("proj:")) S.projs.delete(k.slice(5));
  else if(k.startsWith("ide:")) S.ides.delete(k.slice(4));
  else if(k.startsWith("model:")) S.models.delete(k.slice(6));
  renderAll();
});
let searchT=null;
document.getElementById("search").addEventListener("input",e=>{
  clearTimeout(searchT); const v=e.target.value;
  searchT=setTimeout(()=>{ S.search=v.trim(); renderAll(); },220);
});
document.getElementById("reliableBtn").addEventListener("click",e=>{
  S.exactOnly=!S.exactOnly; e.currentTarget.classList.toggle("on",S.exactOnly); renderAll();
});
document.getElementById("themeBtn").addEventListener("click",cycleTheme);
document.getElementById("settingsBtn").addEventListener("click",openSettings);
document.getElementById("refreshBtn").addEventListener("click",async e=>{
  e.currentTarget.style.opacity=".4";
  await fetch("/api/refresh"); await load(); await loadStorage();
  e.currentTarget.style.opacity="";
});
document.getElementById("liveBtn").addEventListener("click",e=>{
  S.live=!S.live; e.currentTarget.classList.toggle("on",S.live);
  document.getElementById("livedot").classList.toggle("off",!S.live);
});
document.addEventListener("click",e=>{
  const seg=e.target.closest("#metricSeg button,#projMetricSeg button,#provMetricSeg button,#rateSeg button,#ideMetricSeg button");
  if(seg){ const p=seg.parentElement;
    if(p.id==="metricSeg") S.metric=seg.dataset.m;
    if(p.id==="projMetricSeg") S.projMetric=seg.dataset.m;
    if(p.id==="provMetricSeg") S.provMetric=seg.dataset.m;
    if(p.id==="rateSeg") S.rateMetric=seg.dataset.m;
    if(p.id==="ideMetricSeg") S.ideMetric=seg.dataset.m;
    [...p.children].forEach(x=>x.classList.toggle("on",x===seg)); renderAll(); return; }
  const lg=e.target.closest(".li[data-lg]");
  if(lg){ const id=lg.dataset.lg, k=lg.dataset.k;
    S.muted[id]=S.muted[id]||new Set();
    if(S.muted[id].has(k)) S.muted[id].delete(k); else S.muted[id].add(k);
    renderAll(); return; }
  const th=e.target.closest("th[data-k]");
  if(th){ const k=th.dataset.k, t=th.dataset.t;
    const st={sess:S.sessSort,model:S.modelSort,tool:S.toolSort,proj:S.projSort,file:S.fileSort}[t];
    if(st){ st.dir = st.key===k ? -st.dir : -1; st.key=k; renderAll(); } return; }
  const pr=e.target.closest("tr[data-proj]");
  if(pr){ S.projs.clear(); S.projs.add(pr.dataset.proj); S.view="sessions"; renderAll(); return; }
  const sr=e.target.closest("tr[data-sess]");
  if(sr){ openSession(+sr.dataset.sess); return; }
});
document.getElementById("drawerX").addEventListener("click",closeDrawer);
document.getElementById("scrim").addEventListener("click",closeDrawer);
addEventListener("scroll",()=>{
  document.getElementById("filters").classList.toggle("stuck", scrollY>8);
},{passive:true});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change",()=>{
  if((localStorage.getItem("aiu.theme")||"auto")==="auto") applyTheme("auto");
});

/* ---------------- boot ---------------- */
const QP=new URLSearchParams(location.search);
applyTheme(QP.get("theme")||localStorage.getItem("aiu.theme")||"auto");
if(QP.get("range")) S.preset=QP.get("range");
document.getElementById("liveBtn").classList.add("on");
if(location.hash && document.getElementById("v-"+location.hash.slice(1))) S.view=location.hash.slice(1);
load().then(()=>{ if(S.view==="storage") loadStorage(); });
setTimeout(loadStorage, 1200);
/* Poll interval is user-configurable (gear menu). The payload is ~1MB, so a
   tighter loop is real CPU and disk churn; 0 means "only when I press ↻". */
let pollTimers=[];
function applyPollInterval(){
  pollTimers.forEach(clearInterval); pollTimers=[];
  const ms=pollMs();
  if(!ms) return;
  pollTimers.push(setInterval(()=>{ if(S.live) load(); }, ms));
  pollTimers.push(setInterval(()=>{ if(S.live) loadStorage(); }, Math.max(ms*8,120000)));
}
applyPollInterval();

/* ---------------- OPTIMIZE ----------------
   Every finding below is derived from the user's own logs in the selected range
   and must carry (a) a number it is based on and (b) something to actually do.
   A finding that does not apply is not rendered — no filler, no scolding. */

/* sessionRows() adds a display `name`; the finders run on the raw clipped
   sessions, so derive it the same way rather than rendering a blank cell. */
const sessName = s => s.title || s.project || s.id || "(unnamed)";

/* Anthropic bills a cache WRITE at 1.25-2x input and a read at 0.1x. Writing a
   cache you never read back is the one unambiguous waste in the data. */
function findCacheWaste(d){
  const bad = d.sessions.filter(s => s.cc > 20000 && s.cr < s.cc * 0.5);
  if(!bad.length) return null;
  const cc = bad.reduce((a,s)=>a+s.cc,0);
  const est = bad.reduce((a,s)=>a + s.cc/1e6 * 5, 0);   // ~1.25x a $4-ish blended input
  return {
    id:"cache-waste", impact: est, sev: est>5?"high":"low", tools:[...new Set(bad.map(x=>x.source))],
    title:"Cache written but never read back",
    body:`${bad.length} session${bad.length>1?"s":""} wrote <b>${fmtNum(cc)}</b> cache tokens and `
      +`read back less than half of it. A cache write costs 1.25–2× the input rate and only pays `
      +`off when later turns read it — these paid the premium and ended first.`,
    todo:"Usually a session opened, loaded a lot of context, then stopped. Batch related questions "
      +"into one session instead of starting a fresh one per question.",
    rows: bad.sort((a,b)=>b.cc-a.cc).slice(0,5).map(s=>
      [sessName(s), `${fmtNum(s.cc)} written`, `${fmtNum(s.cr)} read`, fmtUSD(s.cost)])
  };
}

/* An expensive model doing light work.

   Nothing here is a hardcoded model list. The candidate replacements are the models
   THIS user actually ran, priced from the rates the server sent, so it stays true as
   models come and go and reflects what they realistically have access to. */
/* NB: a global priceOf() already exists above (RAW.pricing, zero-tuple fallback).
   This one is deliberately separate — it returns null for an unpriced model so the
   savings math can skip it rather than quietly costing it at zero. */
function rateOf(m){ return (RAW.prices && RAW.prices[m]) || null; }
function costAt(m, t){
  const p = rateOf(m); if(!p) return null;
  const [pin,pout,pcw5,pcw1,pcr] = p;
  return ((t.in||0)*pin + (t.out||0)*pout + (t.cr||0)*pcr
        + (t.cc5||t.cc||0)*pcw5 + (t.cc1||0)*pcw1) / 1e6;
}
/* The cheapest model the user ALSO used from the same vendor — a realistic swap,
   not a recommendation to adopt something they've never touched. */
function cheaperPeer(model, usedModels){
  const p = rateOf(model); if(!p || !p[1]) return null;
  const vendor = (RAW.model_vendor||{})[model];
  let best=null, bestOut=p[1];
  for(const m of usedModels){
    if(m===model) continue;
    if((RAW.model_vendor||{})[m] !== vendor) continue;
    const q = rateOf(m);
    if(q && q[1] && q[1] < bestOut){ bestOut=q[1]; best=m; }
  }
  return best;
}
function findModelFit(d){
  const usedModels = [...new Set(d.recs.filter(r=>r.model!=="(user)").map(r=>r.model))];
  const groups = {};
  for(const s of d.sessions){
    if(s.subagent) continue;
    // "light work": short answer, barely any tool use — the shape where a smaller
    // model rarely shows a quality difference. Thresholds are on the session's own
    // output, not on any particular model's name.
    if(!(s.out < 4000 && s.tools <= 3 && s.cost > 0.02)) continue;
    (groups[s.model] = groups[s.model] || []).push(s);
  }
  const rows=[]; let saving=0, n=0, srcs=new Set();
  for(const [model, list] of Object.entries(groups)){
    const alt = cheaperPeer(model, usedModels);
    if(!alt) continue;
    let now=0, then=0;
    for(const s of list){
      const at = costAt(alt, s);
      if(at===null) continue;
      now += s.cost; then += at; srcs.add(s.source);
    }
    if(then >= now || now-then < 0.5) continue;
    saving += now-then; n += list.length;
    rows.push([`${list.length} session${list.length>1?"s":""} on ${model}`,
               `→ ${alt}`, fmtUSD(now)+" spent", fmtUSD(now-then)+" saved"]);
  }
  if(!rows.length) return null;
  rows.sort((a,b)=>parseFloat(b[3].replace(/[^0-9.]/g,""))-parseFloat(a[3].replace(/[^0-9.]/g,"")));
  return {
    id:"model-fit", impact: saving, sev: saving>20?"high":"low", tools:[...srcs],
    title:"A costly model doing very light work",
    body:`${n} session${n>1?"s":""} produced under 4k output tokens with 3 or fewer tool calls, `
      +`yet ran on a model you also pay a premium for. Re-priced at the cheapest model of the `
      +`same maker <i>you already use</i>, the same tokens would have cost `
      +`<b>${fmtUSD(saving)}</b> less.`,
    todo:"For quick lookups and one-shot questions, start the session on the smaller model.",
    rows
  };
}

/* MCP tool definitions ride in the system prompt of EVERY request. A server you
   never call is a standing cost on every turn. */
function findIdleMCP(d){
  if(!RAW.mcp_servers || !RAW.mcp_servers.length) return null;
  const norm = x => String(x||"").toLowerCase().replace(/[^a-z0-9]/g,"");
  const used = {};
  for(const t of RAW.tools){
    if(!t.name.startsWith("mcp__")) continue;
    const rest = t.name.slice(5), i = rest.indexOf("__");
    const srv = norm(i>0 ? rest.slice(0,i) : rest);
    used[srv] = (used[srv]||0) + t.count;
  }
  const wanted = arguments[1];
  const idle = RAW.mcp_servers.filter(m => (m.tool||"claude")===wanted).filter(m => {
    const n = norm(m.name);
    // loose match: a server may appear in tool names under a shortened alias
    return !Object.keys(used).some(u => u===n || u.includes(n) || n.includes(u));
  });
  if(!idle.length) return null;
  const label = (SRC[wanted]||{label:wanted}).label;
  return {
    id:"idle-mcp-"+wanted, impact: 0, sev:"info", tools:[wanted],
    title:`${idle.length} ${label} MCP server${idle.length>1?"s":""} configured but never called`,
    body:`Every connected MCP server's tool definitions are injected into the system prompt of `
      +`<b>every request</b>, whether you use them or not. These ${idle.length} were not called `
      +`once in the selected range: <b>${idle.map(m=>esc(m.name)).join(", ")}</b>.`,
    todo: wanted==="codex"
      ? "Remove the ones you don't reach for from the [mcp_servers.*] blocks in "
        +"~/.codex/config.toml, and re-add when you next need them."
      : "Disconnect the ones you don't reach for — in Claude Code, /mcp, or remove them from "
        +"~/.claude.json. Re-add when you next need them.",
    rows: idle.map(m => [m.name, m.scope==="global"?"global":"project-scoped",
      m.projects && m.projects.length ? m.projects.slice(0,3).join(", ") : "—", "0 calls"])
  };
}

/* Thinking tokens bill at the output rate. Worth surfacing only when the share is
   high enough that dropping effort would actually move the bill. */
function findThinking(d){
  let reason=0, out=0, cost=0;
  for(const r of d.recs){ reason += r.reason||0; out += r.out||0; cost += r.cost||0; }
  if(!out || reason/out < 0.15) return null;
  const share = reason/out;
  return {
    id:"thinking", impact: cost*share*0.3, sev: share>0.3?"high":"low", tools:[...new Set(d.recs.filter(r=>r.reason).map(r=>r.source))],
    title:"Extended thinking is a large share of your output",
    body:`<b>${fmtNum(reason)}</b> of ${fmtNum(out)} output tokens (${fmtPct(share)}) were `
      +`thinking tokens, billed at the full output rate.`,
    todo:"Thinking earns its cost on genuinely hard problems and wastes it on routine edits. "
      +"Lower the default effort and raise it per-task rather than leaving it at max.",
    rows: []
  };
}

/* Sessions where each prompt costs far more than your own norm — usually context
   that grew huge and is now re-read on every single turn. */
function findContextTax(d){
  const withPrompts = d.sessions.filter(s => s.user >= 5 && s.cost > 0);
  if(withPrompts.length < 5) return null;
  const per = withPrompts.map(s => s.cost/s.user).sort((a,b)=>a-b);
  const med = per[Math.floor(per.length/2)];
  const bad = withPrompts.filter(s => s.cost/s.user > Math.max(med*8, 1)).sort((a,b)=>b.cost-a.cost);
  if(!bad.length) return null;
  const excess = bad.reduce((a,s)=>a + (s.cost - med*s.user), 0);
  return {
    id:"context-tax", impact: Math.max(excess,0), sev: excess>50?"high":"low", tools:[...new Set(bad.map(x=>x.source))],
    title:"Long sessions re-reading a very large context",
    body:`${bad.length} session${bad.length>1?"s":""} cost more than 8× your median of `
      +`<b>${fmtUSD2(med)}</b> per prompt. Every turn re-reads the whole conversation, so a session `
      +`that has grown large keeps paying for it on each new question.`,
    todo:"When a session drifts to a new task, start a fresh one — or /compact to drop the "
      +"history you no longer need.",
    rows: bad.slice(0,5).map(s => [sessName(s), `${fmtNum(s.user)} prompts`,
      `${fmtUSD2(s.cost/s.user)}/prompt`, fmtUSD(s.cost)])
  };
}

/* Delegating exploration to a subagent keeps the parent's context small. */
function findSubagents(d){
  const parents = d.sessions.filter(s => !s.subagent);
  // "delegated" shows up two different ways depending on the tool: Claude Code's
  // subagent tokens land back on the parent's own token stream (s.side); Codex's
  // subagents are wholly separate sessions, so the parent only carries a spawn
  // COUNT (s.subagents, from SubAgentActivity "started" markers in its own log).
  const heavy = parents.filter(s => s.tools >= 150 && !s.side && !s.subagents);
  if(heavy.length < 2) return null;
  const spend = heavy.reduce((a,s)=>a+s.cost,0);
  const used = parents.filter(s => s.side > 0 || s.subagents > 0).length;
  return {
    id:"subagents", impact: spend*0.15, sev: spend>100?"high":"low", tools:[...new Set(heavy.map(x=>x.source))],
    title:"Tool-heavy sessions that never delegated to a subagent",
    body:`${heavy.length} session${heavy.length>1?"s":""} made 150+ tool calls without spawning a `
      +`subagent, costing <b>${fmtUSD(spend)}</b>. ${used ? `You do use them elsewhere (${used} `
      +`session${used>1?"s":""} did).` : ""} Every file a search reads lands in the main context and `
      +`is re-read on every later turn; a subagent reads it in its own context and returns only the answer.`,
    todo:"For broad searching or codebase exploration, hand it to a subagent and keep the summary.",
    rows: heavy.sort((a,b)=>b.cost-a.cost).slice(0,5).map(s =>
      [sessName(s), `${fmtNum(s.tools)} tool calls`, `${fmtNum(s.user)} prompts`, fmtUSD(s.cost)])
  };
}

/* Cache hit rate, but only when it is genuinely low — the Cost tab already shows
   the healthy case. */
function findLowCache(d){
  let cr=0, ctx=0;
  for(const r of d.recs){ cr += r.cr||0; ctx += ctxTokens(r); }
  if(!ctx || cr/ctx >= 0.7) return null;
  const rate = cr/ctx;
  return {
    id:"low-cache", impact: 0, sev: rate<0.4?"high":"low", tools:[...new Set(d.recs.filter(r=>r.cr||r.cc).map(r=>r.source))],
    title:"Prompt cache is doing less work than it could",
    body:`Only <b>${fmtPct(rate)}</b> of your context tokens came from cache. A cache read costs `
      +`a tenth of a fresh input token, so the gap is close to pure overhead.`,
    todo:"Caching rewards stable context. Editing files near the top of the conversation, or "
      +"switching models mid-session, invalidates it and forces a re-read.",
    rows: []
  };
}


/* Claude Code stamps attributionSkill on the requests a Skill drove. A skill that
   fires often and pulls a lot of context is worth tightening or scoping. */
function findSkills(d){
  if(!RAW.skills || !RAW.skills.length) return null;
  const inRange = RAW.skills.filter(x => x.date>=d.r.from && x.date<=d.r.to);
  if(!inRange.length) return null;
  const by={};
  for(const x of inRange){
    const e=by[x.name]||(by[x.name]={tok:0,asst:0,cost:0});
    e.tok+=x.tok; e.asst+=x.asst; e.cost+=x.cost;
  }
  const rows=Object.entries(by).sort((a,b)=>b[1].cost-a[1].cost);
  const total=d.recs.reduce((a,r)=>a+(r.cost||0),0);
  const spend=rows.reduce((a,[,v])=>a+v.cost,0);
  if(!spend) return null;
  return {
    id:"skills", impact: 0, sev:"info", tools:["claude"],
    title:"Where your Skills are spending",
    body:`Skills drove <b>${fmtUSD(spend)}</b>${total?` of ${fmtUSD(total)} (${fmtPct(spend/total)})`:""} `
      +`in this range. A skill that runs often carries its whole instruction file into context `
      +`each time, so a frequently-fired one is worth scoping tightly.`,
    todo:"For the expensive ones, narrow when they trigger, or point their subagents at a "
      +"cheaper model.",
    rows: rows.slice(0,6).map(([n,v]) =>
      [`/${n}`, `${fmtNum(v.asst)} requests`, fmtNum(v.tok)+" tokens", fmtUSD(v.cost)])
  };
}

/* Every request re-sends the whole conversation. Past ~150k that is expensive even
   at cache-read rates, because the read itself scales with the context. */
function findBigContext(d){
  if(!RAW.ctx || !RAW.ctx.length) return null;
  const inRange = RAW.ctx.filter(x => x.date>=d.r.from && x.date<=d.r.to);
  if(!inRange.length) return null;
  const by={}; let tot=0; const srcs=new Set();
  for(const x of inRange){ by[x.bucket]=(by[x.bucket]||{tok:0,n:0});
    by[x.bucket].tok+=x.tok; by[x.bucket].n+=x.n; tot+=x.tok;
    if(x.source) srcs.add(x.source); }
  if(!tot) return null;
  const big=(by["150-400k"]?.tok||0)+(by["400k+"]?.tok||0);
  const share=big/tot;
  if(share < 0.4) return null;
  const total=d.recs.reduce((a,r)=>a+(r.cost||0),0);
  const order=["0-50k","50-150k","150-400k","400k+"];
  return {
    id:"big-context", impact: total*share*0.15, sev: share>0.7?"high":"low", tools:[...srcs],
    title:`${fmtPct(share)} of your tokens were sent at over 150k context`,
    body:`Every turn re-sends the whole conversation. Past roughly 150k the re-read dominates `
      +`the bill even when it is cached, because a cache read is charged per token too.`,
    todo: srcs.has("claude")
      ? "/compact once a task is done to drop the history behind it, and /clear (or a fresh "
        +"session) when you switch to something unrelated."
      : "Start a fresh session when you switch tasks rather than continuing a long one — "
        +"the whole history is re-sent on every turn.",
    rows: order.filter(b=>by[b]).map(b =>
      [b+" context", `${fmtNum(by[b].n)} requests`, fmtNum(by[b].tok)+" tokens",
       fmtPct(by[b].tok/tot)])
  };
}

/* Subagents each run their own request loop, so a subagent-heavy day is a real cost
   centre even though it is often the right call. */
function findSubagentShare(d){
  const subs=d.sessions.filter(s=>s.subagent);
  if(!subs.length) return null;
  const subCost=subs.reduce((a,s)=>a+s.cost,0);
  const total=d.sessions.reduce((a,s)=>a+s.cost,0);
  if(!total || subCost/total < 0.2) return null;
  return {
    id:"subagent-share", impact: 0, sev:"info", tools:[...new Set(subs.map(x=>x.source))],
    title:`${fmtPct(subCost/total)} of your spend ran inside subagents`,
    body:`${fmtNum(subs.length)} subagent transcript${subs.length>1?"s":""} cost <b>${fmtUSD(subCost)}</b>. `
      +`Each subagent runs its own request loop with its own context, which is exactly why they keep `
      +`the parent small — but it does mean spawning one is never free.`,
    todo:"Worth it for broad exploration; wasteful for a task you could answer inline. For simple "
      +"delegated work, point the subagent at a cheaper model.",
    rows: subs.sort((a,b)=>b.cost-a.cost).slice(0,5).map(s =>
      [sessName(s), `${fmtNum(s.tools)} tools`, fmtNum(s.tok)+" tokens", fmtUSD(s.cost)])
  };
}


/* Codex pins a global reasoning effort in ~/.codex/config.toml. Worth raising only
   when the setting is at the top of the scale AND the logs show reasoning actually
   dominating the output — otherwise it is someone's deliberate choice, not a finding. */
function findCodexEffort(d){
  const eff = RAW.codex_effort;
  if(!eff || !["high","xhigh","ultra","max"].includes(String(eff).toLowerCase())) return null;
  let reason=0, out=0, cost=0;
  for(const r of d.recs){
    if(r.source!=="codex") continue;
    reason += r.reason||0; out += r.out||0; cost += r.cost||0;
  }
  if(!out || !cost) return null;
  const share = reason/out;
  if(share < 0.2) return null;
  return {
    id:"codex-effort", impact: cost*share*0.25, sev: share>0.5?"high":"low", tools:["codex"],
    title:`Codex reasoning effort is set to "${esc(eff)}"`,
    body:`<b>${fmtNum(reason)}</b> of ${fmtNum(out)} Codex output tokens (${fmtPct(share)}) were `
      +`reasoning, billed at the output rate, against <b>${fmtUSD(cost)}</b> of Codex spend in `
      +`this range. <code>model_reasoning_effort</code> is a global default, so it applies to `
      +`trivial turns as much as hard ones.`,
    todo:"Lower model_reasoning_effort in ~/.codex/config.toml and raise it per-task when a "
      +"problem actually warrants it.",
    rows: []
  };
}

const OPT_FINDERS = [findBigContext, findCacheWaste, findModelFit, findContextTax,
                     findSubagents, findSubagentShare, findSkills, findCodexEffort,
                     d => findIdleMCP(d, "claude"), d => findIdleMCP(d, "codex"),
                     findThinking, findLowCache];

/* Say plainly when a suggestion only applies to one tool — the MCP, Skills and
   /compact advice is Claude Code's, and a Codex or Cursor user should not read it
   as advice about their own setup. Nothing is labelled when it spans every tool
   present in the range, because then the label is noise. */
/* The left stripe carries TOOL identity, using the very same --t-<source> tokens as
   every chart and badge in the app — so orange still means Claude Code and green
   still means Codex here. It is only painted when exactly one tool is in scope;
   a finding spanning several has no single owner and stays neutral.

   It deliberately does NOT encode severity any more. --accent is byte-identical to
   --t-opencode and --warn sits on top of --t-claude-desktop, so a severity stripe
   was painting findings in tool colours that had nothing to do with the tool. */
function stripeClass(f){
  const t=(f.tools||[]).filter(Boolean);
  return t.length===1 && SRC[t[0]] ? "" : " sev-neutral";
}
function stripeStyle(f){
  const t=(f.tools||[]).filter(Boolean);
  if(t.length!==1 || !SRC[t[0]]) return "";
  return ` style="border-left-color:var(${SRC[t[0]].v})"`;
}

function scopeLabel(f){
  const t = (f.tools||[]).filter(Boolean);
  if(!t.length) return "";
  const present = new Set(SLICE_SOURCES);
  if(t.length >= present.size && [...present].every(x=>t.includes(x))) return "";
  const names = t.map(x=>(SRC[x]||{label:x}).label);
  // "X only" reads right for a single tool; for several it is nonsense ("A only,
  // B only"), so just name them.
  const text = names.length === 1 ? names[0] + " only" : names.join(" · ");
  return `<div class="finding-scope"><span class="scope-chip">${esc(text)}</span></div>`;
}
let SLICE_SOURCES=[];

function viewOptimize(d){
  SLICE_SOURCES=[...new Set(d.recs.map(r=>r.source))];
  const found = OPT_FINDERS.map(f => { try{ return f(d); }catch(e){ return null; } })
                           .filter(Boolean)
                           .sort((a,b) => b.impact - a.impact);
  const totalSpend = d.recs.reduce((a,r)=>a+(r.cost||0),0);
  const addressable = found.reduce((a,f)=>a+f.impact, 0);
  document.getElementById("optStats").innerHTML = [
    {l:"Spend in range", v:fmtUSD(totalSpend), s:`${fmtNum(d.sessions.length)} sessions`},
    {l:"Potentially addressable", v:addressable>0?fmtUSD(addressable):"—",
     s:totalSpend?`about ${fmtPct(addressable/totalSpend)} of spend`:"—"},
    {l:"Findings", v:String(found.length), s:found.length?"ranked by est. saving":"nothing to flag"},
  ].map(x=>`<div class="stat" style="flex:1 1 150px"><div class="l">${x.l}</div>
      <div class="v num">${x.v}</div><div class="s">${esc(x.s)}</div></div>`).join("");

  document.getElementById("optHint").textContent =
    `derived from your own logs · ${fmtNum(d.sessions.length)} sessions in the selected range`;

  const host = document.getElementById("optFindings");
  if(!found.length){
    host.innerHTML = `<div class="empty">Nothing worth flagging in this range — your cache hit
      rate, model mix and session lengths all look reasonable.</div>`;
    return;
  }
  host.innerHTML = found.map(f => `
    <div class="finding${stripeClass(f)}"${stripeStyle(f)}>
      <div class="finding-head">
        <div>
          <div class="finding-title">${f.title}</div>
          ${scopeLabel(f)}
        </div>
        ${f.impact > 0.5 ? `<div class="finding-impact num">${fmtUSD(f.impact)}<span>est. saving</span></div>` : ""}
      </div>
      <div class="finding-body">${f.body}</div>
      <div class="finding-todo"><b>What to do</b> ${f.todo}</div>
      ${f.rows && f.rows.length ? `<table class="finding-tbl"><tbody>${
        f.rows.map(r=>`<tr>${r.map((c,i)=>
          `<td${i===0?' class="name"':(i===r.length-1?' class="num r"':' class="dim"')}>${esc(c)}</td>`
        ).join("")}</tr>`).join("")}</tbody></table>` : ""}
    </div>`).join("");
}
