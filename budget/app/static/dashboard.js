const D = JSON.parse(document.getElementById('data').textContent);
const won = n => '₩' + Math.round(n).toLocaleString('ko-KR');
const man = n => {
  const a = Math.abs(n);
  if (a >= 1e8) return (n/1e8).toFixed(1).replace(/\.0$/,'') + '억';
  if (a >= 1e4) return Math.round(n/1e4).toLocaleString('ko-KR') + '만';
  return Math.round(n).toLocaleString('ko-KR');
};
const el = (t, cls, html) => { const e = document.createElement(t); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

/* ── 툴팁 ─────────────────────────────────── */
const tip = document.getElementById('tip');
document.addEventListener('pointerover', e => {
  const t = e.target.closest('[data-tip]');
  if (!t) return;
  tip.innerHTML = t.dataset.tip;
  tip.style.opacity = 1;
});
document.addEventListener('pointermove', e => {
  if (tip.style.opacity !== '1') return;
  const pad = 14, r = tip.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
});
document.addEventListener('pointerout', e => {
  if (e.target.closest('[data-tip]')) tip.style.opacity = 0;
});

/* ── KPI ─────────────────────────────────── */
// (시안에 있던 파일 상태 표시는 서버가 직접 렌더링한다)

const s = D.summary, pv = D.prev;
const kpiDefs = [
  { k:'수입',    c:'var(--income)', v:s['수입'],     p:pv['수입'],     goodUp:true  },
  { k:'지출',    c:'var(--spend)',  v:s['지출'],     p:pv['지출'],     goodUp:false },
  { k:'저축·투자', c:'var(--save)',   v:s['저축투자'], p:pv['저축투자'], goodUp:true  },
];
const kwrap = document.getElementById('kpis');
kpiDefs.forEach(d => {
  const diff = d.v - d.p;
  const good = diff === 0 ? null : (diff > 0) === d.goodUp;
  const arrow = diff > 0 ? '▲' : diff < 0 ? '▼' : '–';
  const cls = good === null ? '' : good ? 'up' : 'down';
  const c = el('div','kpi');
  c.innerHTML = `<div class="lab"><i class="dot" style="background:${d.c}"></i>${d.k}</div>
    <div class="val">${won(d.v)}</div>
    <div class="delta">전월 대비 <b class="${cls}">${arrow} ${man(Math.abs(diff))}</b></div>`;
  kwrap.appendChild(c);
});
const rate = s['저축률'], prate = pv['저축률'], rdiff = rate - prate;
const kr = el('div','kpi');
kr.innerHTML = `<div class="lab">저축률 <span style="color:var(--muted)">(수입−지출)/수입</span></div>
  <div class="val hero">${(rate*100).toFixed(0)}%</div>
  <div class="delta">전월 대비 <b class="${rdiff>=0?'up':'down'}">${rdiff>=0?'▲':'▼'} ${Math.abs(rdiff*100).toFixed(0)}%p</b></div>`;
kwrap.appendChild(kr);

/* ── 정제 리포트 ─────────────────────────── */
const cw = document.getElementById('clean');
const exc = D.excluded, excTotal = Object.values(exc).reduce((a,b)=>a+b,0);
[['제외한 금액 (1년)', excTotal, '뱅샐이 지출로 세던 금액'],
 ...Object.entries(exc).map(([k,v]) => [k, v, ''])].forEach(([k,v,note]) => {
  cw.appendChild(el('div','clean-item',
    `<div class="n">${won(v)}</div><div class="k">${k}${note?`<br><span style="color:var(--muted)">${note}</span>`:''}</div>`));
});
cw.appendChild(el('div','clean-item',
  `<div class="n" style="color:var(--warning)">${D.unclassified_n}건</div>
   <div class="k">아직 미분류<br><span style="color:var(--muted)">원본 342건에서 줄인 값</span></div>`));

/* ── 가로 막대 3블록 ─────────────────────── */
function block(cls, title, color, cats) {
  const entries = Object.entries(cats).filter(([,v]) => v > 0).sort((a,b) => b[1]-a[1]);
  const total = entries.reduce((a,[,v]) => a+v, 0);
  const max = Math.max(...entries.map(([,v]) => v), 1);
  const b = el('div','block ' + cls);
  b.appendChild(el('h3', null, `<i class="dot" style="background:${color}"></i>${title}`));
  b.appendChild(el('div','tot', won(total)));
  const rows = el('div','rows');
  entries.forEach(([name, v]) => {
    const r = el('div','row');
    r.innerHTML = `<div class="name">${name}</div>
      <div class="track"><div class="bar" style="width:${Math.max(v/max*100,1.5)}%;background:${color}"
        data-tip="<b>${name}</b><br>${won(v)}<br>${title} 중 ${(v/total*100).toFixed(0)}%"></div></div>
      <div class="amt">${man(v)}</div>`;
    rows.appendChild(r);
  });
  if (!entries.length) rows.appendChild(el('div','row','<div class="name"></div><div style="color:var(--muted);font-size:12.5px">없음</div><div></div>'));
  b.appendChild(rows);
  return b;
}
const three = document.getElementById('three');
three.appendChild(block('income','이달 수입','var(--income)', D.income_cat));
three.appendChild(block('spend','이달 지출','var(--spend)', {...D.fixed_cat, ...D.var_cat}));
three.appendChild(block('save','이달 저축·투자','var(--save)', D.save_cat));

/* ── 월별 추이 ───────────────────────────── */
(function trend() {
  const rows = D.series.filter(r => r.수입 + r.지출 > 100000);
  const W = 1000, H = 300, ml = 62, mr = 82, mt = 14, mb = 34;
  const iw = W - ml - mr, ih = H - mt - mb;
  const max = Math.max(...rows.flatMap(r => [r.수입, r.지출, r.저축투자]));
  const step = Math.pow(10, Math.floor(Math.log10(max)));
  const top = Math.ceil(max / (step/2)) * (step/2);
  const x = i => ml + (rows.length === 1 ? iw/2 : i * iw / (rows.length - 1));
  const y = v => mt + ih - (v / top) * ih;

  const series = [
    { k:'수입', key:'수입', c:'var(--income)' },
    { k:'지출', key:'지출', c:'var(--spend)' },
    { k:'저축·투자', key:'저축투자', c:'var(--save)' },
  ];
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="월별 수입·지출·저축 추이">`;
  for (let t = 0; t <= 4; t++) {
    const v = top * t / 4, yy = y(v);
    svg += `<line class="gridline" x1="${ml}" x2="${ml+iw}" y1="${yy}" y2="${yy}"/>
            <text class="tick" x="${ml-9}" y="${yy+4}" text-anchor="end">${man(v)}</text>`;
  }
  svg += `<line class="axisline" x1="${ml}" x2="${ml+iw}" y1="${y(0)}" y2="${y(0)}"/>`;
  rows.forEach((r, i) => {
    const m = +r.month.slice(5);
    svg += `<text class="tick" x="${x(i)}" y="${H-12}" text-anchor="middle">${m}월</text>`;
  });
  series.forEach(sr => {
    const d = rows.map((r,i) => `${i?'L':'M'}${x(i)},${y(r[sr.key])}`).join(' ');
    svg += `<path class="line" d="${d}" stroke="${sr.c}"/>`;
  });
  series.forEach(sr => {
    rows.forEach((r,i) => {
      svg += `<circle cx="${x(i)}" cy="${y(r[sr.key])}" r="4.5" fill="${sr.c}"
        stroke="var(--surface)" stroke-width="2"/>`;
    });
    const last = rows.length - 1;
    svg += `<text class="lbl" x="${x(last)+11}" y="${y(rows[last][sr.key])+4}">${sr.k}</text>`;
  });
  // 히트 영역 (열 단위 크로스헤어)
  rows.forEach((r,i) => {
    const half = iw / (rows.length - 1) / 2;
    const tipHtml = `<b>${r.month}</b><br>수입 ${won(r.수입)}<br>지출 ${won(r.지출)}<br>저축·투자 ${won(r.저축투자)}<br>저축률 ${(r.저축률*100).toFixed(0)}%`;
    svg += `<rect x="${x(i)-half}" y="${mt}" width="${half*2}" height="${ih}" fill="transparent"
      data-tip="${tipHtml.replace(/"/g,'&quot;')}"/>`;
  });
  svg += `</svg>`;
  document.getElementById('trend').innerHTML = svg;

  let t = `<table><thead><tr><th>월</th><th>수입</th><th>지출</th><th>저축·투자</th><th>저축률</th></tr></thead><tbody>`;
  rows.forEach(r => {
    t += `<tr><td>${r.month}</td><td>${won(r.수입)}</td><td>${won(r.지출)}</td><td>${won(r.저축투자)}</td><td>${(r.저축률*100).toFixed(0)}%</td></tr>`;
  });
  document.getElementById('trendtable').innerHTML = t + '</tbody></table>';
})();

/* ── 카테고리별 지출 (소유자 스택) ───────── */
(function bycat() {
  const box = document.getElementById('bycat');
  if (!box) return;
  const owners = D.owners || [];
  const colors = ['var(--spend)', 'var(--spend-2)'];
  const rows = (D.by_owner || []).filter(r => r.total > 0);
  const max = Math.max(...rows.map(r => r.total), 1);

  rows.forEach(row => {
    const segs = owners.map((o, i) => {
      const v = row[o] || 0;
      if (!v) return '';
      return `<i style="background:${colors[i % colors.length]};flex:${v}"
                 data-tip="<b>${row.category}</b> · ${o}<br>${won(v)}"></i>`;
    }).join('');
    const el = document.createElement('div');
    el.className = 'stack-row';
    el.innerHTML = `<div class="name" style="color:var(--ink-2);text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${row.category}</div>
      <div class="stack" style="width:${Math.max(row.total / max * 100, 2)}%">${segs}</div>
      <div class="amt" style="font-variant-numeric:tabular-nums;font-size:12px">${man(row.total)}</div>`;
    box.appendChild(el);
  });
  if (!rows.length) box.innerHTML = '<p class="note">이 달에는 지출이 없습니다.</p>';
})();

/* ── 고정비 · 변동비 ─────────────────────── */
(function fixvar() {
  const f = Object.values(D.fixed_cat).reduce((a,b)=>a+b,0);
  const v = Object.values(D.var_cat).reduce((a,b)=>a+b,0);
  const tot = f + v || 1;
  document.getElementById('fixvar').innerHTML = `
    <div class="stack" style="height:26px;margin-bottom:12px">
      <i style="background:var(--spend);flex:${f};height:26px" data-tip="고정비<br>${won(f)} · ${(f/tot*100).toFixed(0)}%"></i>
      <i style="background:var(--spend-2);flex:${v};height:26px" data-tip="변동비<br>${won(v)} · ${(v/tot*100).toFixed(0)}%"></i>
    </div>
    <div class="legend" style="margin:0">
      <span><i class="key sq" style="background:var(--spend)"></i>고정비 ${won(f)} · ${(f/tot*100).toFixed(0)}%</span>
      <span><i class="key sq" style="background:var(--spend-2)"></i>변동비 ${won(v)} · ${(v/tot*100).toFixed(0)}%</span>
    </div>`;
  const tb = document.getElementById('fixtable');
  Object.entries(D.fixed_cat).sort((a,b)=>b[1]-a[1]).forEach(([k,val]) => {
    tb.appendChild(el('tr', null, `<td>${k}</td><td>${won(val)}</td>`));
  });
})();

/* ── 순자산 ─────────────────────────────── */
(function nw() {
  const a = D.assets, l = D.liabilities, n = D.net;
  const max = Math.max(a, l, 1);
  const inv = D.investments.reduce((s,i) => s + (i['평가금액']||0), 0);
  const cost = D.investments.reduce((s,i) => s + (i['원금']||0), 0);
  const ret = cost ? (inv - cost) / cost : 0;
  document.getElementById('networth').innerHTML = `
    <div style="font-size:34px;font-weight:650;letter-spacing:-.02em;margin-bottom:4px">${won(n)}</div>
    <div style="font-size:12.5px;color:var(--ink-2);margin-bottom:18px">총자산 ${won(a)} − 총부채 ${won(l)}</div>
    <div class="rows">
      <div class="row"><div class="name">총자산</div>
        <div class="track"><div class="bar" style="width:${a/max*100}%;background:var(--save)" data-tip="총자산<br>${won(a)}"></div></div>
        <div class="amt">${man(a)}</div></div>
      <div class="row"><div class="name">총부채</div>
        <div class="track"><div class="bar" style="width:${l/max*100}%;background:var(--debt)" data-tip="총부채<br>${won(l)}"></div></div>
        <div class="amt">${man(l)}</div></div>
    </div>
    <div style="display:flex;gap:26px;flex-wrap:wrap;margin-top:18px;font-size:12.5px">
      <div><div style="color:var(--ink-2)">투자 평가금액</div><div style="font-size:17px;font-weight:600">${won(inv)}</div></div>
      <div><div style="color:var(--ink-2)">투자 수익률</div><div style="font-size:17px;font-weight:600" class="${ret>=0?'up':'down'}">${ret>=0?'+':''}${(ret*100).toFixed(1)}%</div></div>
      <div><div style="color:var(--ink-2)">대출 잔액</div><div style="font-size:17px;font-weight:600">${won(D.loans.reduce((s,x)=>s+(x['잔액']||0),0))}</div></div>
    </div>`;
})();

/* ── 정리 큐 ─────────────────────────────── */
(function queue() {
  const q = document.getElementById('queue');
  if (!q) return;
  (D.unclassified || []).forEach(u => {
    q.appendChild(el('div', 'qitem',
      `<div class="ic">❓</div>
       <div class="body"><div class="t">${u.content}</div>
       <div class="d">${u.count}건 · ${won(u.amount)} — 카테고리를 한 번만 정해주면 전부 같이 분류됩니다.</div></div>
       <span class="pill">정리 화면에서</span>`));
  });
})();
