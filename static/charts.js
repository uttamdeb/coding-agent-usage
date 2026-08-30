/* ==========================================================================
   charts.js — Chart.js theming + the chart/SVG builders.
   Marks are thin, grids are hairlines, stacked segments carry a 2px surface
   gap, and every multi-series chart ships an interactive legend (identity is
   never colour-alone).
   ========================================================================== */
const charts = {};

function theme(){
  return {
    text:   cssv("--text"),
    text2:  cssv("--text-2"),
    text3:  cssv("--text-3"),
    grid:   cssv("--grid"),
    surface:cssv("--surface"),
    border: cssv("--border-2"),
    accent: cssv("--accent"),
  };
}
function mk(id, cfg){
  const el = document.getElementById(id);
  if(!el) return;
  if(charts[id]) charts[id].destroy();
  const T = theme();
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
  Chart.defaults.color = T.text3;
  cfg.options = cfg.options || {};
  cfg.options.responsive = true;
  cfg.options.maintainAspectRatio = false;
  cfg.options.animation = {duration:220};
  cfg.options.plugins = deepMerge({
    legend:{display:false},
    tooltip:{
      backgroundColor:T.surface, titleColor:T.text, bodyColor:T.text2,
      borderColor:T.border, borderWidth:1, padding:10, cornerRadius:9,
      displayColors:true, boxWidth:8, boxHeight:8, boxPadding:4,
      titleFont:{weight:"600",size:11.5}, bodyFont:{size:11.5},
      usePointStyle:true, itemSort:(a,b)=>b.parsed.y-a.parsed.y,
    }
  }, cfg.options.plugins||{});
  charts[id] = new Chart(el, cfg);
  return charts[id];
}
function axes(o){
  const T = theme();
  const base = {
    x:{grid:{display:false,drawBorder:false},border:{display:false},
       ticks:{color:T.text3,font:{size:10.5},maxRotation:0,autoSkipPadding:14}},
    y:{grid:{color:T.grid,drawBorder:false},border:{display:false},
       ticks:{color:T.text3,font:{size:10.5},padding:6}},
  };
  return deepMerge(base, o||{});
}
function deepMerge(a,b){
  const out=Object.assign({},a);
  for(const k in b){
    out[k] = (b[k] && typeof b[k]==="object" && !Array.isArray(b[k]))
      ? deepMerge(a[k]||{}, b[k]) : b[k];
  }
  return out;
}
/* stacked-bar dataset with the mandated 2px surface gap between segments */
function stackDS(label, data, color){
  return {label, data, backgroundColor:color, stack:"a",
    borderColor:cssv("--surface"), borderWidth:{top:2,left:0,right:0,bottom:0},
    borderRadius:3, borderSkipped:false, maxBarThickness:34};
}
/* A line needs TWO points to draw a segment, so a single-day range (or a series
   with one lone reading) renders as empty axes. Show the point itself instead. */
function soloPoint(data){
  return (data||[]).filter(v=>v!=null && v!==undefined).length < 2 ? 4 : 0;
}
function areaDS(label, data, color, fill){
  return {label, data, borderColor:color, backgroundColor:color+"59",
    borderWidth:2, pointRadius:soloPoint(data), pointHoverRadius:4, tension:.25,
    fill:fill===undefined?true:fill, stack:"a"};
}
/* value labels at the bar ends — small horizontal bar lists are unreadable
   without them once one series dwarfs the rest */
const barLabels = {
  id:"barLabels",
  afterDatasetsDraw(c, a, o){
    const {ctx}=c, meta=c.getDatasetMeta(0);
    ctx.save();
    ctx.font="600 10.5px "+getComputedStyle(document.body).fontFamily;
    ctx.textBaseline="middle";
    const inkOut=cssv("--text-2"), inkIn=cssv("--surface");
    meta.data.forEach((bar,i)=>{
      const v=c.data.datasets[0].data[i];
      if(v==null) return;
      const txt=(o.fmt||fmtTok)(v);
      const pad=6;
      const fits = bar.x + pad + ctx.measureText(txt).width < c.chartArea.right;
      ctx.textAlign = fits ? "left" : "right";
      ctx.fillStyle = fits ? inkOut : inkIn;   // inside a bar, use the surface ink
      ctx.fillText(txt, fits ? bar.x+pad : bar.x-pad, bar.y);
    });
    ctx.restore();
  }
};
function hbar(id, rows, opt){
  opt = opt || {};
  const fmt = opt.fmt || fmtTok;
  mk(id, {
    plugins:[barLabels],
    type:"bar",
    data:{labels:rows.map(r=>r.label),
      datasets:[{data:rows.map(r=>r.value),
        backgroundColor:rows.map(r=>r.color||cssv("--accent")),
        borderRadius:3, borderSkipped:false, maxBarThickness:opt.thick||16}]},
    options:{indexAxis:"y",
      layout:{padding:{right:opt.padRight||54}},
      scales:axes({x:{grid:{display:true,color:cssv("--grid")},ticks:{color:cssv("--text-3"),
                       font:{size:10.5},callback:v=>fmt(v)}},
                   y:{grid:{display:false},ticks:{color:cssv("--text-2"),font:{size:11},
                       crossAlign:"far",autoSkip:false}}}),
      plugins:{tooltip:{itemSort:null,callbacks:{
        label:c=>" "+fmt(c.parsed.x)+(opt.sub?opt.sub(rows[c.dataIndex]):"")},},
        barLabels:{fmt}}}
  });
}

/* ---------- activity calendar (SVG, sequential blue) ---------- */
function renderCalendar(byDay, maxV, onClick){
  const wrap = document.getElementById("calWrap");
  const end = new Date(); end.setHours(0,0,0,0);
  const start = new Date(end); start.setDate(start.getDate()-364);
  start.setDate(start.getDate()-((start.getDay()+6)%7));      // back to Monday
  const cell=12, gap=3, step=cell+gap, mT=18, mL=26;
  const weeks=[]; let cur=new Date(start);
  while(cur<=end){ const wk=[]; for(let i=0;i<7;i++){ wk.push(new Date(cur)); cur.setDate(cur.getDate()+1); } weeks.push(wk); }
  const ramp = seqRamp();
  const bucket = v => { if(!v) return ramp[0];
    const q=v/(maxV||1);
    return q>.6?ramp[6]:q>.3?ramp[5]:q>.12?ramp[4]:q>.03?ramp[2]:ramp[1]; };
  const W = mL+weeks.length*step+8, H = mT+7*step+6;
  let s=`<svg width="${W}" height="${H}" role="img" aria-label="activity calendar">`;
  let lastM=-1;
  weeks.forEach((wk,wi)=>{ const m=wk[0].getMonth();
    if(m!==lastM){ lastM=m;
      s+=`<text x="${mL+wi*step}" y="10" fill="${cssv("--text-3")}" font-size="10">${MONTHS[m]}</text>`; }});
  [["Mon",0],["Wed",2],["Fri",4]].forEach(([l,r])=>{
    s+=`<text x="0" y="${mT+r*step+10}" fill="${cssv("--text-3")}" font-size="9.5">${l}</text>`; });
  weeks.forEach((wk,wi)=>wk.forEach((d,di)=>{
    if(d>end) return;
    const k=dkey(d), v=byDay[k]||0;
    s+=`<rect x="${mL+wi*step}" y="${mT+di*step}" width="${cell}" height="${cell}" rx="3"
        fill="${bucket(v)}" data-day="${k}" data-v="${v}" style="cursor:pointer"/>`;
  }));
  s+="</svg>";
  wrap.innerHTML=s;
  const svg=wrap.querySelector("svg");
  svg.addEventListener("mousemove",e=>{
    const r=e.target.closest("rect"); if(!r) return hideTip();
    const v=+r.dataset.v;
    showTip(`<div class="t">${r.dataset.day}</div><div class="r"><span class="k">tokens</span>
      <span class="v">${v?fmtTok(v):"none"}</span></div>`, e.clientX, e.clientY);
  });
  svg.addEventListener("mouseleave",hideTip);
  svg.addEventListener("click",e=>{ const r=e.target.closest("rect"); if(r&&onClick) onClick(r.dataset.day); });
}

/* ---------- hour x weekday heatmap (SVG, sequential blue) ---------- */
function renderHeatmap(cells, maxV){
  const cw=100/24, gap=2, rowH=22, mL=32, mT=16;
  const ramp=seqRamp();
  const bucket=v=>{ if(!v) return ramp[0]; const q=v/(maxV||1);
    return q>.66?ramp[6]:q>.4?ramp[5]:q>.2?ramp[4]:q>.07?ramp[3]:q>.01?ramp[2]:ramp[1]; };
  const W=760, H=mT+7*rowH+14, cellW=(W-mL)/24;
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMinYMin meet" role="img" aria-label="activity by hour and weekday">`;
  for(let h=0;h<24;h+=3)
    s+=`<text x="${mL+h*cellW}" y="10" fill="${cssv("--text-3")}" font-size="9.5">${pad2(h)}</text>`;
  for(let d=0;d<7;d++){
    s+=`<text x="0" y="${mT+d*rowH+15}" fill="${cssv("--text-3")}" font-size="9.5">${DOW[d]}</text>`;
    for(let h=0;h<24;h++){
      const v=(cells[d]&&cells[d][h])||0;
      s+=`<rect x="${mL+h*cellW}" y="${mT+d*rowH}" width="${cellW-gap}" height="${rowH-gap}" rx="3"
          fill="${bucket(v)}" data-d="${d}" data-h="${h}" data-v="${v}"/>`;
    }
  }
  s+="</svg>";
  const wrap=document.getElementById("heatmap"); wrap.innerHTML=s;
  const svg=wrap.querySelector("svg");
  svg.addEventListener("mousemove",e=>{
    const r=e.target.closest("rect"); if(!r) return hideTip();
    showTip(`<div class="t">${DOW[+r.dataset.d]} ${pad2(+r.dataset.h)}:00</div>
      <div class="r"><span class="k">tokens</span><span class="v">${fmtTok(+r.dataset.v)}</span></div>`,
      e.clientX,e.clientY);
  });
  svg.addEventListener("mouseleave",hideTip);
}

/* ---------- small horizontal share bars (HTML) ---------- */
function shareBars(el, rows, fmt){
  const total = rows.reduce((a,r)=>a+r.value,0) || 1;
  el.innerHTML = rows.map(r=>`
    <div style="margin-bottom:11px">
      <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
        <span style="display:flex;align-items:center;gap:7px">
          <span class="swatch" style="background:${r.color};margin:0"></span>${esc(r.label)}</span>
        <span class="muted num">${fmt(r.value)} · ${fmtPct(r.value/total)}</span>
      </div>
      <div class="meter"><i style="width:${(r.value/total*100).toFixed(1)}%;background:${r.color}"></i></div>
    </div>`).join("") || `<div class="empty">Nothing in range.</div>`;
}
