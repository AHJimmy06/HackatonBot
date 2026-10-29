/* Vista Analítica de CTT Connect: negocio + calidad del chat + motor. Sin dependencias. */
(() => {
  const el = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const fmt = (n, suffix = '') => (n === null || n === undefined ? '—' : `${Number(n).toLocaleString('es-EC')}${suffix}`);
  const money = (n) => (n ? `$${Number(n).toLocaleString('es-EC', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '$0');
  let days = 30;
  let includeTests = true;
  let showDailyTable = false;
  let last = null;

  async function json(path, options = {}) {
    const res = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  }

  // ---------------------------------------------------------------- tooltip compartido
  const tip = document.createElement('div');
  tip.className = 'an-tip';
  tip.hidden = true;
  document.body.appendChild(tip);
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = `${Math.min(x + 14, window.innerWidth - w - 8)}px`;
    tip.style.top = `${Math.max(y - h - 10, 8)}px`;
  }
  document.addEventListener('mousemove', (ev) => {
    const target = ev.target.closest && ev.target.closest('[data-tip]');
    if (target && target.closest('#analytics-view')) showTip(target.dataset.tip, ev.clientX, ev.clientY);
    else if (!ev.target.closest || !ev.target.closest('.an-line')) tip.hidden = true;
  });

  // ---------------------------------------------------------------- piezas
  const kpi = (label, value, note) => `<div class="an-kpi"><span>${esc(label)}</span><strong>${value}</strong><small>${esc(note)}</small></div>`;

  function hbars(rows, max, colorOf) {
    if (!rows.length) return '<div class="an-empty">Sin datos en el período.</div>';
    return rows.map((r) => {
      const segs = r.segments.map((s) => `<div class="seg" style="width:${max ? (100 * s.n) / max : 0}%;background:${colorOf(s)}"
        data-tip="<b>${esc(r.label)}</b><br>${esc(s.name)}: ${fmt(s.n)}"></div>`).join('');
      return `<div class="an-hbar"><span class="lbl" title="${esc(r.label)}">${esc(r.label)}</span><div class="track">${segs}</div>
        <span class="val">${r.value}</span></div>`;
    }).join('');
  }

  function lineChart(rows, series) {
    const W = 640, H = 220, L = 30, R = 92, T = 10, B = 24;
    const max = Math.max(1, ...rows.flatMap((r) => series.map((s) => r[s.key])));
    const step = (W - L - R) / Math.max(rows.length - 1, 1);
    const x = (i) => L + i * step;
    const y = (v) => T + (H - T - B) * (1 - v / max);
    const ticks = [0, Math.ceil(max / 2), max];
    const grid = ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>`).join('');
    const yAxis = ticks.map((t) => `<text x="${L - 6}" y="${y(t) + 3}" text-anchor="end">${t}</text>`).join('');
    const xAxis = rows.map((r, i) => (i % 2 === 0 || i === rows.length - 1)
      ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(r.date.slice(8))}/${esc(r.date.slice(5, 7))}</text>` : '').join('');
    // etiquetas directas al final, separadas para que no choquen
    const ends = series.map((s) => ({ s, y: y(rows.at(-1)[s.key]) })).sort((a, b) => a.y - b.y);
    ends.forEach((e, i) => { if (i && e.y - ends[i - 1].y < 12) e.y = ends[i - 1].y + 12; });
    const paths = series.map((s) => `<path d="${rows.map((r, i) => `${i ? 'L' : 'M'}${x(i)},${y(r[s.key])}`).join('')}" stroke="${s.color}"/>`).join('');
    const labels = ends.map((e) => `<text class="endlabel" x="${W - R + 8}" y="${e.y + 3}">${esc(e.s.name)} · ${fmt(rows.at(-1)[e.s.key])}</text>`).join('');
    return `<svg class="an-line" viewBox="0 0 ${W} ${H}" role="img" aria-label="Actividad diaria">
      <g class="grid">${grid}</g><g class="axis">${yAxis}${xAxis}</g><g class="series">${paths}</g>${labels}
      <line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>
      <rect class="hit" x="${L}" y="0" width="${W - L - R}" height="${H - B}" fill="transparent"/></svg>`;
  }

  function bindLine(container, rows, series) {
    const svg = container.querySelector('.an-line');
    if (!svg) return;
    const cross = svg.querySelector('.cross');
    const W = 640, L = 30, R = 92;
    const step = (W - L - R) / Math.max(rows.length - 1, 1);
    svg.querySelector('.hit').addEventListener('mousemove', (ev) => {
      const box = svg.getBoundingClientRect();
      const vx = ((ev.clientX - box.left) / box.width) * W;
      const i = Math.max(0, Math.min(rows.length - 1, Math.round((vx - L) / step)));
      const cx = L + i * step;
      cross.setAttribute('x1', cx); cross.setAttribute('x2', cx); cross.setAttribute('visibility', 'visible');
      showTip(`<b>${esc(rows[i].date)}</b><br>` + series.map((s) => `<i style="background:${s.color}"></i>${esc(s.name)}: ${fmt(rows[i][s.key])}`).join('<br>'),
        ev.clientX, ev.clientY);
    });
    svg.querySelector('.hit').addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); tip.hidden = true; });
  }

  // ---------------------------------------------------------------- render
  const MOODS = [
    { key: 'caliente', name: 'Lead caliente', icon: '🔥', color: 'var(--status-good)' },
    { key: 'interesado', name: 'Interesado', icon: '●', color: 'var(--series-1)' },
    { key: 'dudoso', name: 'Dudoso', icon: '▲', color: 'var(--status-warning)' },
    { key: 'frustrado', name: 'Frustrado', icon: '■', color: 'var(--status-critical)' },
  ];
  const DAILY = [
    { key: 'conversations', name: 'Conversaciones', color: 'var(--series-1)' },
    { key: 'recomendado', name: 'Recomendaciones', color: 'var(--series-2)' },
    { key: 'clic_inscripcion', name: 'Clics inscribirme', color: 'var(--series-3)' },
  ];

  function render(info, d) {
    const k = d.kpis;
    const root = el('analytics-view');
    const modeOptions = Object.entries(info.modes).map(([key, label]) =>
      `<option value="${esc(key)}" ${key === info.mode ? 'selected' : ''}>${esc(label)}</option>`).join('');
    const funnelMax = d.funnel[0].n;
    const funnel = hbars(d.funnel.map((f) => ({
      label: f.step, segments: [{ name: f.step, n: f.n }],
      value: `${fmt(f.n)} <small>${funnelMax ? Math.round((100 * f.n) / funnelMax) : 0}%</small>`,
    })), funnelMax, () => 'var(--brand)');
    const areaMax = Math.max(0, ...d.areas.map((a) => a.interested));
    const areas = hbars(d.areas.map((a) => ({
      label: a.label, value: `${fmt(a.interested)}${a.without_offer ? ` <small>${a.without_offer} sin oferta</small>` : ''}`,
      segments: [{ name: 'Con curso vigente', n: a.interested - a.without_offer, kind: 1 }, { name: 'Sin curso vigente', n: a.without_offer, kind: 2 }]
        .filter((s) => s.n > 0),
    })), areaMax, (s) => (s.kind === 1 ? 'var(--series-1)' : 'var(--series-2)'));
    const qMax = Math.max(0, ...d.questions.map((q) => q.asked));
    const questions = hbars(d.questions.map((q) => ({
      label: q.label, segments: [{ name: 'Veces preguntado', n: q.asked }],
      value: `${fmt(q.asked)}${q.missing ? ` <small>${q.missing} sin dato</small>` : ''}`,
    })), qMax, () => 'var(--brand)');
    const moodTotal = MOODS.reduce((acc, m) => acc + (d.quality.moods[m.key] || 0), 0);
    const moodBar = moodTotal ? MOODS.filter((m) => d.quality.moods[m.key]).map((m) =>
      `<div style="width:${(100 * d.quality.moods[m.key]) / moodTotal}%;background:${m.color}"
        data-tip="${m.icon} <b>${esc(m.name)}</b>: ${d.quality.moods[m.key]} (${Math.round((100 * d.quality.moods[m.key]) / moodTotal)}%)"></div>`).join('') : '';
    const moodLegend = MOODS.map((m) => `<span class="an-status"><i style="background:${m.color}"></i>${m.icon} ${esc(m.name)} · ${fmt(d.quality.moods[m.key] || 0)}</span>`).join('');
    const reasons = d.handoff_reasons.length ? d.handoff_reasons.map((r) => `<tr><td>${esc(r.reason)}</td><td class="num">${fmt(r.n)}</td></tr>`).join('')
      : '<tr><td colspan="2" class="an-empty">Ninguna derivación.</td></tr>';
    const modes = d.by_mode.length ? d.by_mode.map((m) => `<tr><td>${esc(m.label)}</td><td class="num">${fmt(m.conversations)}</td>
      <td class="num">${fmt(m.recommend_rate, '%')}</td><td class="num">${fmt(m.signup_rate, '%')}</td><td class="num">${fmt(m.handoff_rate, '%')}</td>
      <td class="num">${fmt(m.frustration_rate, '%')}</td><td class="num">${fmt(m.csat, '%')}</td><td class="num">${m.avg_latency_ms ? `${fmt(m.avg_latency_ms)} ms` : '—'}</td></tr>`).join('')
      : '<tr><td colspan="8" class="an-empty">Aún no hay conversaciones en ningún modo.</td></tr>';
    const courses = d.courses.length ? d.courses.map((c) => `<tr><td>${esc(c.title)}</td><td class="num">${fmt(c.recommended)}</td>
      <td class="num">${fmt(c.questions)}</td><td class="num">${fmt(c.signup_clicks)}</td><td class="num">${fmt(c.enrolled)}</td>
      <td class="num">${fmt(c.notify_requests)}</td><td class="num">${money(c.revenue)}</td></tr>`).join('')
      : '<tr><td colspan="7" class="an-empty">Sin actividad por curso.</td></tr>';
    const leads = d.leads.length ? d.leads.map((l) => `<tr><td>${esc(l.name)}</td><td>${esc(l.kind)}</td><td>${esc(l.course)}</td>
      <td>${esc(new Date(l.at).toLocaleString('es-EC', { timeZone: 'America/Guayaquil' }))}</td>
      <td class="num">${l.kind === 'Quiere inscribirse' && l.course_slug ? `<button data-enroll="${l.conversation_id}:${esc(l.course_slug)}">Marcar inscrito</button>` : ''}</td></tr>`).join('')
      : '<tr><td colspan="5" class="an-empty">Sin leads pendientes.</td></tr>';
    const dailyTable = `<table class="an-table"><tr><th>Fecha</th>${DAILY.map((s) => `<th class="num">${esc(s.name)}</th>`).join('')}<th class="num">Inscritos</th></tr>
      ${d.daily.map((r) => `<tr><td>${esc(r.date)}</td>${DAILY.map((s) => `<td class="num">${fmt(r[s.key])}</td>`).join('')}<td class="num">${fmt(r.inscrito)}</td></tr>`).join('')}</table>`;

    root.innerHTML = `<div class="an-root">
      <div class="an-toolbar">
        <select id="an-days" aria-label="Período">${[7, 30, 90].map((n) => `<option value="${n}" ${n === days ? 'selected' : ''}>Últimos ${n} días</option>`).join('')}</select>
        <label class="an-chip">Motor para chats nuevos <select id="an-mode" aria-label="Modo del motor">${modeOptions}</select></label>
        <span class="an-chip">Jev <b class="${info.jev ? 'ok' : 'off'}">${info.jev ? '● conectado' : '○ sin clave'}</b></span>
        <span class="an-chip">LLM <b class="${info.llm ? 'ok' : 'off'}">${info.llm ? '● conectado' : '○ sin clave'}</b></span>
        <label class="an-chip"><input type="checkbox" id="an-tests" ${includeTests ? 'checked' : ''}> Incluir pruebas del simulador
          ${d.test_conversations ? `<b>(${d.test_conversations})</b>` : ''}</label>
        <span class="an-spacer"></span><button id="an-refresh">↻ Actualizar</button>
      </div>
      <div class="an-kpis">
        ${kpi('Prospectos', fmt(k.prospects), `Conversaciones en ${d.period_days} días`)}
        ${kpi('Conversión a inscripción', fmt(k.conversion_rate, '%'), `${fmt(k.signup_clicks)} tocaron «Inscribirme»`)}
        ${kpi('Inscritos confirmados', fmt(k.enrolled), `${money(k.revenue)} en tarifas publicadas`)}
        ${kpi('Satisfacción', fmt(k.csat, '%'), `${fmt(k.ratings)} calificaciones 👍/👎 · índice estimado ${fmt(k.satisfaction_index)}/100`)}
        ${kpi('Resueltas sin humano', fmt(k.automation_rate, '%'), `${fmt(k.handoff_rate, '%')} pasó a un asesor`)}
        ${kpi('Frustración detectada', fmt(k.frustration_rate, '%'), k.operator_wait_min !== null ? `Espera a asesor: ${fmt(k.operator_wait_min)} min` : 'Sin esperas registradas')}
      </div>
      <div class="an-grid">
        <section class="an-card third"><h3>Qué decidir ahora</h3><p class="an-sub">Lecturas automáticas de los datos del período</p>
          <ul class="an-insights">${d.insights.map((i) => `<li>${esc(i)}</li>`).join('')}</ul></section>
        <section class="an-card two"><h3>Embudo de venta</h3><p class="an-sub">De la primera conversación a la inscripción confirmada por un asesor</p>${funnel}</section>
        <section class="an-card two" id="an-daily"><h3>Actividad diaria <button class="an-toggle" id="an-daily-toggle">${showDailyTable ? 'Ver gráfico' : 'Ver tabla'}</button></h3>
          <p class="an-sub">Últimos ${d.daily.length} días</p>
          <div class="an-legend">${DAILY.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>
          ${showDailyTable ? dailyTable : lineChart(d.daily, DAILY)}</section>
        <section class="an-card third"><h3>Calidad del chat</h3><p class="an-sub">Estado de ánimo detectado por conversación</p>
          <div class="an-moodbar">${moodBar}</div><div class="an-legend">${moodLegend}</div>
          <div class="an-signals">
            <div class="an-signal"><b>${fmt(d.quality.thumbs_up)} / ${fmt(d.quality.thumbs_down)}</b><span>👍 / 👎 en recomendaciones</span></div>
            <div class="an-signal"><b>${fmt(d.quality.frustration_events)}</b><span>Señales de frustración</span></div>
            <div class="an-signal"><b>${fmt(d.quality.not_understood)}</b><span>Mensajes no entendidos</span></div>
            <div class="an-signal"><b>${fmt(d.quality.off_topic)}</b><span>Fuera de tema</span></div>
            <div class="an-signal"><b>${fmt(d.quality.blocked_attempts)}</b><span>Intentos de manipulación frenados</span></div>
          </div></section>
        <section class="an-card"><h3>Demanda por área</h3><p class="an-sub">Lo que buscan los prospectos frente a la oferta vigente</p>
          <div class="an-legend"><span><i style="background:var(--series-1)"></i>Con curso vigente</span><span><i style="background:var(--series-2)"></i>Sin curso vigente</span></div>${areas}</section>
        <section class="an-card"><h3>Lo que más preguntan</h3><p class="an-sub">«Sin dato» = la ficha oficial no lo publica</p>${questions}</section>
        <section class="an-card wide"><h3>Cursos</h3><p class="an-sub">Recomendaciones, intención y ventas por curso</p>
          <table class="an-table"><tr><th>Curso</th><th class="num">Recomendado</th><th class="num">Preguntas</th><th class="num">Clic inscribirme</th>
          <th class="num">Inscritos</th><th class="num">Pidieron aviso</th><th class="num">Ingreso</th></tr>${courses}</table></section>
        <section class="an-card two"><h3>Leads para seguimiento</h3><p class="an-sub">Quisieron inscribirse o pidieron aviso y no figuran como inscritos</p>
          <table class="an-table"><tr><th>Prospecto</th><th>Acción</th><th>Curso</th><th>Cuándo</th><th></th></tr>${leads}</table></section>
        <section class="an-card third"><h3>Motivos de derivación</h3><p class="an-sub">Por qué el chat pasó a un asesor</p>
          <table class="an-table">${reasons}</table></section>
        <section class="an-card wide"><h3>Comparación de modos del motor</h3><p class="an-sub">Mismo negocio, distinto cerebro: decide cuál dejar en producción</p>
          <table class="an-table"><tr><th>Modo</th><th class="num">Conversaciones</th><th class="num">Llegan a recomendación</th><th class="num">Tocan inscribirme</th>
          <th class="num">Pasan a humano</th><th class="num">Frustración</th><th class="num">Satisfacción</th><th class="num">Latencia media</th></tr>${modes}</table></section>
      </div></div>`;

    if (!showDailyTable) bindLine(el('an-daily'), d.daily, DAILY);
    el('an-days').onchange = (ev) => { days = Number(ev.target.value); load(); };
    el('an-refresh').onclick = () => load();
    el('an-tests').onchange = (ev) => { includeTests = ev.target.checked; load(); };
    el('an-daily-toggle').onclick = () => { showDailyTable = !showDailyTable; render(info, d); };
    el('an-mode').onchange = async (ev) => {
      await json('/api/motor/mode', { method: 'POST', body: JSON.stringify({ mode: ev.target.value }) });
      load();
    };
    root.querySelectorAll('[data-enroll]').forEach((b) => {
      b.onclick = async () => {
        const [cid, slug] = b.dataset.enroll.split(':');
        b.disabled = true;
        await json('/api/motor/enrolled', { method: 'POST', body: JSON.stringify({ conversation_id: Number(cid), course_slug: slug }) });
        load();
      };
    });
  }

  async function load() {
    const root = el('analytics-view');
    if (!root) return;
    if (!last) root.innerHTML = '<div class="an-empty">Cargando analítica…</div>';
    try {
      const [info, data] = await Promise.all([json('/api/motor'), json(`/api/motor/analytics?days=${days}&tests=${includeTests ? 1 : 0}`)]);
      last = data;
      render(info, data);
    } catch (err) {
      root.innerHTML = `<div class="an-empty">No se pudo cargar la analítica: ${esc(err.message)}</div>`;
    }
  }

  window.CTTAnalytics = { load };
})();
