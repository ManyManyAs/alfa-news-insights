// Визуальные блоки вместо текста: пульс ленты, цепочка влияния, «деньги на кону» в виде диаграммы,
// шкала «что на кону» в разборе и лента дат календаря. Чистый SVG/HTML, цвета — токены темы (светлая и тёмная).
/* global esc, rub, plural, fmtDate, EFFECT */

const EFF_ORDER = ["risk", "mixed", "watch", "opportunity"];
const EFF_COLOR = { risk: "var(--risk)", mixed: "var(--mixed)", watch: "var(--watch)", opportunity: "var(--opp)" };
const EFF_WORD = { risk: "риски", mixed: "смешанные", watch: "отслеживать", opportunity: "возможности" };

function isoDay(d) { return d.toISOString().slice(0, 10); }

// Пульс ленты: события по дням за 14 дней, столбцы по эффекту. Клик по дню — лента этого дня.
function pulseChart(feed, days = 14) {
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const recent = feed.filter((it) => (it.published_at || "") >= isoDay(new Date(today - (days - 1) * 864e5))).length;
  // в тихие периоды дневные столбцы пустые — тогда показываем 12 недель
  const weekly = recent < 3, step = weekly ? 7 : 1, n = weekly ? 12 : days;
  const cols = [];
  for (let k = n - 1; k >= 0; k--) {
    const end = new Date(today - k * step * 864e5), start = new Date(end - (step - 1) * 864e5);
    const a = isoDay(start), b = isoDay(end);
    const items = feed.filter((it) => { const d = (it.published_at || "").slice(0, 10); return d >= a && d <= b; });
    cols.push({ key: weekly ? "" : b, d: end, start, items, by: Object.fromEntries(EFF_ORDER.map((e) => [e, items.filter((it) => (it.effect || "watch") === e).length])) });
  }
  days = n;
  const older = feed.filter((it) => (it.published_at || "") < isoDay(cols[0].start)).length;
  const max = Math.max(1, ...cols.map((c) => c.items.length));
  const W = 420, H = 120, B = 20, gap = 6, bw = (W - gap * (days - 1)) / days;
  const bars = cols.map((c, i) => {
    let y = H - B;
    const x = i * (bw + gap);
    const segs = EFF_ORDER.filter((e) => c.by[e]).map((e) => {
      const h = Math.max(3, (c.by[e] / max) * (H - B - 8));
      y -= h;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${(h - 1).toFixed(1)}" rx="2.5" fill="${EFF_COLOR[e]}"/>`;
    }).join("");
    const when = weekly ? `неделя ${c.start.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} – ${c.d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}`
      : c.d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
    const tip = `${when}: ${c.items.length ? EFF_ORDER.filter((e) => c.by[e]).map((e) => `${EFF_WORD[e]} — ${c.by[e]}`).join(", ") : "событий нет"}`;
    const lbl = i % 3 === (days - 1) % 3 ? `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 5}" text-anchor="middle" class="pc-ax">${weekly ? c.d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "") : c.d.getDate()}</text>` : "";
    const empty = c.items.length ? "" : `<rect x="${x.toFixed(1)}" y="${H - B - 3}" width="${bw.toFixed(1)}" height="3" rx="1.5" class="pc-empty"/>`;
    const click = c.items.length && c.key;
    return `<g class="pc-col${click ? " on" : ""}" ${click ? `data-pulse-day="${c.key}" tabindex="0" role="button"` : ""} aria-label="${esc(tip)}">
      <rect x="${(x - gap / 2).toFixed(1)}" y="0" width="${(bw + gap).toFixed(1)}" height="${H - B}" class="pc-hit"/>${segs}${empty}${lbl}<title>${esc(tip)}</title></g>`;
  }).join("");
  const total = cols.reduce((s, c) => s + c.items.length, 0);
  const legend = EFF_ORDER.filter((e) => feed.some((it) => it.effect === e)).map((e) => `<span><i style="background:${EFF_COLOR[e]}"></i>${EFF_WORD[e]}</span>`).join("");
  return `<div class="mini-head"><h3>Пульс ленты</h3><span class="muted small">${total} ${weekly ? "за 12 недель" : "за 14 дней"}${older ? ` · ещё ${older} раньше` : ""}</span></div>
    <svg viewBox="0 0 ${W} ${H}" class="pulse-svg" role="img" aria-label="События ленты по дням за две недели">${bars}</svg>
    <div class="pc-legend">${legend}</div>`;
}

// Цепочка влияния для карточки «Главное сейчас»: событие → фактор → через что задевает компанию → деньги
function impactChain(it, money, moneyText) {
  const r = it.reasons[0] || {};
  const via = (r.why || "").split(" — ")[0].replace(/^У компании /, "").replace(/^Компания /, "");
  const dir = r.direction === "up" ? "↑" : r.direction === "down" ? "↓" : "";
  const node = (k, t, cls = "") => `<div class="ch-node ${cls}"><span>${k}</span><b>${esc(t)}</b></div>`;
  const arrow = `<svg class="ch-arrow" viewBox="0 0 24 12" aria-hidden="true"><path d="M0 6h20M15 1l6 5-6 5"/></svg>`;
  return `<div class="chain" title="${esc(r.why || "")}">
    ${node("событие", EFFECT[it.effect] || "", "eff-" + it.effect)}${arrow}
    ${node("фактор", `${r.factor_title || "—"} ${dir}`.trim())}${arrow}
    ${node("через", via ? via[0].toUpperCase() + via.slice(1) : "профиль компании")}${arrow}
    ${node("за 12 месяцев", money ? moneyText(money).replace(/<[^>]+>/g, "") : "без заметного эффекта", money ? "money" : "calm")}
  </div>`;
}

// «Деньги на кону»: по рычагам — минус влево (красный), плюс вправо (зелёный), длина — сумма
function moneyBars(levers) {
  const max = Math.max(1, ...levers.flatMap((v) => [Math.abs(v.down || 0), Math.abs(v.up || 0)]));
  return `<div class="mbars">${levers.map((v, i) => {
    const dw = Math.abs(v.down || 0) / max * 100, uw = Math.abs(v.up || 0) / max * 100;
    return `<div class="mb-row">
      <div class="mb-label"><b>${esc(v.title)}</b><button class="mb-ev link-btn" data-mb="${i}">${v.events.length} ${plural(v.events.length, "событие", "события", "событий")}</button></div>
      <div class="mb-bar" title="${esc(`${v.title}: от ${rub(v.down || 0)} до ${rub(v.up || 0)} за 12 месяцев`)}">
        <div class="mb-half neg">${dw ? `<i style="width:${dw.toFixed(1)}%"></i><em>${rub(v.down)}</em>` : ""}</div>
        <div class="mb-zero"></div>
        <div class="mb-half pos">${uw ? `<i style="width:${uw.toFixed(1)}%"></i><em>${rub(v.up)}</em>` : ""}</div>
      </div>
      <div class="mb-more" hidden>${v.events.map((e) => `<button class="link-btn" data-money-ev="${esc(e.event_id)}">${esc(e.title)}</button>`).join("")}
        ${v.action ? `<div class="ml-act">Как ограничить: ${esc(v.action)}</div>` : ""}</div>
    </div>`;
  }).join("")}<div class="mb-axis"><span>минус к плану</span><span>прогноз банка = 0</span><span>плюс к плану</span></div></div>`;
}

// Шкала «что на кону» в разборе: диапазон от негативного до позитивного сценария, база (прогноз банка) — ноль
function stakeBar(lo, hi) {
  const m = Math.max(Math.abs(lo), Math.abs(hi), 1);
  const l = 50 - Math.abs(Math.min(lo, 0)) / m * 50, r = 50 + Math.max(hi, 0) / m * 50;
  return `<div class="stake" role="img" aria-label="${esc(`От ${rub(lo)} до ${rub(hi)}, прогноз банка — без изменений к плану`)}">
    <div class="stake-track"><i class="neg" style="left:${l.toFixed(1)}%;width:${(50 - l).toFixed(1)}%"></i><i class="pos" style="left:50%;width:${(r - 50).toFixed(1)}%"></i><b style="left:50%"></b></div>
    <div class="stake-lbl"><span>${rub(lo)}</span><span>0 — прогноз банка</span><span>${rub(hi)}</span></div></div>`;
}

// Лента 90 дней над списком календаря: точки дат по типу, клик — к дате в списке
function calendarStrip(items, today, until) {
  const t0 = new Date(today), t1 = new Date(until), span = Math.max(1, (t1 - t0) / 864e5);
  const pos = (d) => Math.min(100, Math.max(0, (new Date(d) - t0) / 864e5 / span * 100));
  const months = [];
  for (let d = new Date(t0.getFullYear(), t0.getMonth() + 1, 1); d <= t1; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) months.push(d);
  return `<div class="cal-strip" role="img" aria-label="Ключевые даты на 90 дней">
    <div class="cs-line"></div>
    ${months.filter((d) => pos(d) > 7).map((d) => `<span class="cs-month" style="left:${pos(d).toFixed(1)}%">${d.toLocaleDateString("ru-RU", { month: "short" })}</span>`).join("")}
    <span class="cs-today" style="left:0">сегодня</span>
    ${items.map((x, i) => `<button class="cs-dot ${x.kind}" style="left:${pos(x.date).toFixed(1)}%" data-cs="${i}" title="${esc(`${fmtDate(x.date)} — ${x.title}`)}" aria-label="${esc(`${fmtDate(x.date)} — ${x.title}`)}"></button>`).join("")}
  </div>`;
}
