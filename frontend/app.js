// Прототип интерфейса «Бизнес-радар» в интернет-банке. Без сборки и зависимостей.
const state = { users: [], roles: {}, user: null, company: null, role: null, feed: [], current: null, insight: null };
const $ = (s) => document.querySelector(s);
const EFFECT = { risk: "риск", opportunity: "возможность", mixed: "риски и возможности", watch: "отслеживать" };
const KIND = { fact: "Факт", assumption: "Предположение источника", bank_view: "Позиция Альфа-Банка", ai_inference: "Вывод сервиса (AI)" };
const DIR = { up: "↑", down: "↓", stable: "→", unknown: "·" };
const CONF = { high: "уверенность высокая", medium: "уверенность средняя", low: "уверенность низкая" };
const TRUST = { official: ["официально", "Регулятор или орган власти"], agency: ["агентство", "Информационное агентство"],
  media: ["СМИ", "Деловое СМИ: мнения и прогнозы помечаются как предположения"], user: ["ваша новость", "Добавлено пользователем"],
  demo: ["демо", "Синтетический демо-пример"] };
function plural(n, one, few, many) { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many; }
function effectiveTag(e) {
  if (!e) return "";
  const when = e.days === 0 ? "сегодня" : `через ${e.days} ${plural(e.days, "день", "дня", "дней")}`;
  return `<span class="pill due ${e.days <= 30 ? "soon" : ""}" title="Дата вступления в силу: ${esc(e.date)}">⏱ вступает в силу ${when}</span>`;
}
function entityChips(it) {
  const t = TRUST[it.trust] || TRUST.media;
  return `<span class="chip trust ${esc(it.trust)}" title="${esc(t[1])}">${esc(t[0])}</span>`
    + (it.regions || []).slice(0, 2).map((r) => `<span class="chip geo">📍 ${esc(r)}</span>`).join("")
    + (it.industries || []).slice(0, 2).map((x) => `<span class="chip ind" title="ОКВЭД ${esc(x.code)}">${esc(x.name)}</span>`).join("");
}
function storyLine(s, current) {
  return `<ol class="timeline">${s.timeline.map((e) => `<li class="${e.event_id === current ? "cur" : ""}" data-story-ev="${esc(e.event_id)}">
    <span class="muted small">${fmtDate(e.published_at)}</span> ${e.direction ? `<b>${DIR[e.direction] || ""}</b>` : ""} ${esc(e.title)}</li>`).join("")}</ol>`;
}

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmtDate(s) { if (!s) return ""; const d = new Date(s); return isNaN(d) ? s : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }); }

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.user) headers["X-User-Id"] = state.user;
  const r = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (!r.ok) { const t = await r.text(); throw new Error(`${r.status}: ${t}`); }
  return r.json();
}

// SSE поверх fetch (EventSource не умеет заголовки X-User-Id)
async function sse(path, opts, onEvent) {
  const headers = { "Content-Type": "application/json", "X-User-Id": state.user };
  const r = await fetch(path, { ...opts, headers });
  if (!r.ok) { throw new Error(`${r.status}: ${await r.text()}`); }
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
      if (line.startsWith("data:")) onEvent(JSON.parse(line.slice(5)));
    }
  }
}

const STEP_RU = { relevance: "Оценка релевантности", plan: "План анализа", draft: "Черновик интерпретации", finalize: "Итог",
  "repair:rules": "Удаление неподтверждённого", revise: "Правка по замечаниям", preview: "Быстрый вывод по профилю",
  limit: "Лимит времени или стоимости — дальше без LLM", "tool:scenario_calc": "Расчёт сценариев",
  "tool:get_story_timeline": "Хронология сюжета", "tool:get_regulation_dates": "Даты вступления в силу" };
function stepTitle(name) {
  if (STEP_RU[name]) return STEP_RU[name];
  if (name.startsWith("tool:")) return "Инструмент: " + name.slice(5);
  if (name.startsWith("critique:llm")) return "Проверка LLM-критиком";
  if (name.startsWith("critique:rules")) return "Самопроверка правилами";
  return name;
}

function q(params) { return new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "")).toString(); }

// ---------------------------------------------------------------- init / сессия
async function init() {
  const { users, roles } = await api("/api/demo-users");
  state.users = users; state.roles = roles;
  $("#userSelect").innerHTML = users.map((u) => `<option value="${u.user_id}">${esc(u.label)} (${u.user_id})</option>`).join("");
  $("#roleSelect").innerHTML = `<option value="">Без роли</option>` + Object.entries(roles).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#userSelect").onchange = () => selectUser($("#userSelect").value);
  $("#companySelect").onchange = () => { state.company = $("#companySelect").value; reload(); };
  $("#roleSelect").onchange = () => { state.role = $("#roleSelect").value || null; reload(); };
  document.querySelectorAll("[data-view]").forEach((el) => el.onclick = () => show(el.dataset.view));
  document.querySelectorAll("[data-goto]").forEach((el) => el.onclick = () => show(el.dataset.goto));
  $("#analyzeBtn").onclick = () => $("#analyzeDialog").showModal();
  bindFeedControls();
  $("#tourBtn").onclick = startTour;
  $("#themeBtn").onclick = toggleTheme;
  document.querySelectorAll("[data-dper]").forEach((b) => b.onclick = () => {
    state.digestPeriod = b.dataset.dper;
    document.querySelectorAll("[data-dper]").forEach((x) => x.classList.toggle("on", x === b));
    loadDigest();
  });
  document.querySelectorAll(".nav-item[data-view]").forEach((el) => { el.tabIndex = 0; el.setAttribute("role", "link");
    el.onkeydown = (e) => { if (e.key === "Enter") show(el.dataset.view); }; });
  $("#analyzeSubmit").onclick = analyzeNews;
  if (users.length) await selectUser(users[0].user_id);
  maybeOnboard();
}

async function selectUser(uid) {
  state.user = uid;
  const u = state.users.find((x) => x.user_id === uid);
  const me = await api("/api/me");
  $("#companySelect").innerHTML = me.companies.map((c) => `<option value="${c.uid}">${esc(c.name)}</option>`).join("");
  state.company = u?.primary_company || me.companies[0]?.uid;
  $("#companySelect").value = state.company;
  state.role = u?.role || null;
  $("#roleSelect").value = state.role || "";
  await reload();
}

function show(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  document.querySelectorAll(".nav-item[data-view]").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  if (view === "metrics") loadMetrics();
  if (view === "sources") loadSources();
  if (view === "digest") loadDigest();
}

async function reload() {
  state.insight = null; state.current = null;
  $("#insightPanel").innerHTML = `<div class="empty">Выберите событие — сервис объяснит, что оно значит именно для вашей компании.</div>`;
  await Promise.all([loadProfile(), loadFeed(), state.bankLoaded ? null : loadBank()]);
  if ($("#view-digest").classList.contains("active")) loadDigest();
}

// ---------------------------------------------------------------- позиция банка (макропрогноз)
const KEY_IND = [["key_rate_end", "2026"], ["key_rate_end", "2027"], ["inflation_dec", "2026"], ["usd_rub", "2026"], ["gdp", "2026"], ["wages_nominal", "2026"]];
function fmtVal(v, unit) { const n = (unit.startsWith("%") ? Number(v).toFixed(1) : String(v)).replace(".", ","); return unit.startsWith("%") ? `${n}%${unit.slice(1)}` : `${n} ${unit}`; }
async function loadBank() {
  const d = await api("/api/bank/forecasts");
  state.bankLoaded = true;
  const by = Object.fromEntries(d.forecasts.map((f) => [`${f.indicator}|${f.period}`, f]));
  const rows = KEY_IND.map(([i, p]) => by[`${i}|${p}`]).filter(Boolean).map((f) => {
    const r = f.revision;
    const arrow = r ? `<span class="rev ${r.direction}" title="Банк ${r.direction === "up" ? "повысил" : "понизил"} прогноз: ${fmtVal(r.from, f.unit)} → ${fmtVal(r.to, f.unit)} (${fmtDate(r.from_date)} → ${fmtDate(r.to_date)})">${r.direction === "up" ? "▲" : "▼"} было ${fmtVal(r.from, f.unit)}</span>` : "";
    return `<tr><td>${esc(f.title)}, ${f.period}</td><td class="num">${fmtVal(f.value, f.unit)}</td><td>${arrow}</td></tr>`;
  }).join("");
  const pts = (d.rate_path && d.rate_path.points) || [];
  let spark = "";
  if (pts.length > 1) {
    const vals = pts.map((p) => p[1]), lo = Math.min(...vals) - 0.5, hi = Math.max(...vals) + 0.5, W = 260, H = 70;
    const xy = pts.map((p, i) => [12 + i * (W - 24) / (pts.length - 1), H - 14 - (p[1] - lo) / (hi - lo) * (H - 28)]);
    spark = `<svg viewBox="0 0 ${W} ${H}" class="spark" role="img" aria-label="Прогноз ключевой ставки">
      <polyline points="${xy.map((p) => p.join(",")).join(" ")}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>
      ${xy.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="var(--accent)"/><text x="${p[0]}" y="${p[1] - 7}" text-anchor="middle">${String(pts[i][1]).replace(".", ",")}%</text><text x="${p[0]}" y="${H - 1}" text-anchor="middle" class="lbl">${esc(pts[i][0])}</text>`).join("")}</svg>`;
  }
  $("#bankCard").innerHTML = `<div class="eyebrow">Позиция Альфа-Банка</div>
    <h3>Макропрогноз банка</h3>
    ${spark ? `<div class="muted small">Траектория ключевой ставки (прогноз от ${fmtDate(d.rate_path.date)})</div>${spark}` : ""}
    <table class="fc">${rows}</table>
    <div class="muted small">${d.theses_count} тезисов из ${d.documents.length} аналитических материалов · ▲▼ — пересмотр прогноза банком</div>`;
}

// ---------------------------------------------------------------- профиль
async function loadProfile() {
  const { profile: p, exposures, peers, overrides, factors } = await api(`/api/companies/${state.company}?${q({ role: state.role })}`);
  state.factors = factors; state.exposures = exposures;
  renderRadar();
  const money = (v) => v == null ? "нет данных" : (v >= 1e9 ? (v / 1e9).toFixed(1) + " млрд ₽" : (v / 1e6).toFixed(1) + " млн ₽");
  const products = { CL_PRODUCT_RKO_MARK: "РКО", CL_PRODUCT_LOA_MARK: "Кредит", CL_PRODUCT_LEA_MARK: "Лизинг", CL_PRODUCT_DEP_MARK: "Депозит", CL_PRODUCT_ZPP_MARK: "Зарплатный проект", CL_PRODUCT_GAR_MARK: "Гарантии", FACTORING_MARK: "Факторинг", FTS_FLAG: "ВЭД" };
  $("#profileCard").innerHTML = `
    <div class="eyebrow">Профиль, на котором строится персонализация</div>
    <h3>${esc(p.okved_name)}</h3>
    <dl>
      <dt>ОКВЭД</dt><dd>${esc(p.okved)}</dd>
      <dt>Регион</dt><dd>${esc(p.region || "—")}</dd>
      <dt>Выручка</dt><dd>${p.revenue_suspicious ? "недостоверна" : money(p.revenue)}</dd>
      <dt>Сотрудники</dt><dd>${esc(p.workers || "—")}</dd>
      <dt>Стадия</dt><dd>${esc(p.lifecycle_stage || "—")}</dd>
      <dt>ВЭД</dt><dd>${p.fts ? esc(p.fts_countries.join(", ") || "есть") : "нет"}</dd>
      <dt>Доля в отрасли*</dt><dd>${p.okved_revenue_share != null ? (p.okved_revenue_share * 100).toFixed(2) + "% из " + p.okved_peers : "—"}</dd>
      <dt>Группа</dt><dd>${p.group_size} юрлиц${p.group_revenue_share != null ? ", доля " + (p.group_revenue_share * 100).toFixed(0) + "%" : ""}</dd>
    </dl>
    <div class="chips">${p.products.map((x) => `<span class="chip">${products[x] || x}</span>`).join("")}</div>
    <div class="sub-h">Чувствительность к факторам <span class="muted small">— нажмите, чтобы сделать тему важнее или скрыть</span></div>
    <div class="chips">${exposures.slice(0, 8).map((e) => interestChip(e.factor, e.title, e.weight, e.override, e.reasons)).join("")}</div>
    ${overrides.length ? `<div class="overrides">${overrides.map((o) => `<span class="ov ${o.multiplier === 0 ? "mute" : o.multiplier > 1 ? "more" : "less"}">${esc(o.title)}: ${o.multiplier === 0 ? "скрыто" : o.source === "manual" ? "важнее" : "×" + o.multiplier}<span class="muted"> · ${o.source === "manual" ? "вы настроили" : "по вашим оценкам"}</span></span>`).join("")}
      <button class="btn ghost small" id="resetPrefs">Сбросить настройки</button></div>` : ""}
    ${supplyChain(p)}
    ${peersBlock(peers)}
    ${p.data_gaps.length ? `<div class="notice">Неполные данные: ${esc(p.data_gaps.join("; "))}</div>` : ""}
    <div class="muted small">* оценка по выборке клиентов с тем же ОКВЭД</div>`;
  bindInterests();
}

function mixBars(rows, unknown) {
  const all = [...rows];
  if (unknown > 0.03) all.push({ name: "вне выборки (отрасль неизвестна)", share: unknown, unknown: true });
  return all.map((m) => `<div class="mix-row${m.unknown ? " unk" : ""}"><span>${esc(m.name)}</span><i style="width:${Math.max(3, m.share * 100)}%"></i><b>${Math.round(m.share * 100)}%</b></div>`).join("");
}
const LEVEL_NEXT = { normal: "more", more: "mute", mute: "normal" };
const LEVEL_ICON = { more: "⬆ ", mute: "🔇 ", normal: "" };
function levelOf(o) { return !o ? "normal" : o.source !== "manual" ? "normal" : o.multiplier === 0 ? "mute" : "more"; }
function interestChip(factor, title, weight, o, reasons) {
  const lvl = levelOf(o);
  const tip = `${(reasons || []).filter((r) => r.why).map((r) => r.why).join("; ")}\nКлик: ${{ normal: "сделать важнее", more: "скрыть тему", mute: "вернуть как было" }[lvl]}`;
  return `<button class="chip interest ${lvl}" data-factor="${esc(factor)}" data-level="${lvl}" title="${esc(tip)}">${LEVEL_ICON[lvl]}${esc(title)} · ${weight}</button>`;
}
function bindInterests() {
  document.querySelectorAll(".chip.interest").forEach((el) => el.onclick = async () => {
    const level = LEVEL_NEXT[el.dataset.level];
    await api(`/api/companies/${state.company}/preferences`, { method: "PUT", body: JSON.stringify({ factor: el.dataset.factor, level }) });
    toast(level === "mute" ? "Тема скрыта из ленты" : level === "more" ? "Будем показывать эту тему выше" : "Тема снова как обычно");
    await Promise.all([loadProfile(), loadFeed()]);
  });
  const r = document.getElementById("resetPrefs");
  if (r) r.onclick = async () => { await api(`/api/companies/${state.company}/preferences`, { method: "DELETE" }); toast("Настройки ленты сброшены"); await Promise.all([loadProfile(), loadFeed()]); };
}
function toast(text) {
  let t = document.getElementById("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = text; t.className = "show";
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.className = ""; }, 3500);
}

function supplyChain(p) {
  const buyers = p.industry_mix.filter((m) => m.direction === "in"), suppliers = p.industry_mix.filter((m) => m.direction === "out");
  if (!buyers.length && !suppliers.length && !p.n_buyers && !p.n_suppliers) return "";
  const conc = (h) => h == null ? "—" : h > 0.5 ? "очень высокая" : h > 0.25 ? "высокая" : h > 0.1 ? "умеренная" : "низкая";
  return `<div class="sub-h">Цепочка поставок (граф платежей)</div>
    ${buyers.length || p.unknown_share_in ? `<div class="muted small">Покупатели · ${p.n_buyers} · концентрация ${conc(p.hhi_in)}</div>${mixBars(buyers, p.unknown_share_in || 0)}` : ""}
    ${suppliers.length || p.unknown_share_out ? `<div class="muted small">Поставщики · ${p.n_suppliers} · концентрация ${conc(p.hhi_out)}</div>${mixBars(suppliers, p.unknown_share_out || 0)}` : ""}
    <div class="chips">${p.network_pct != null ? `<span class="chip" title="PageRank в сети денежных потоков">Узел сети: выше ${Math.round(p.network_pct * 100)}% компаний</span>` : ""}
      ${p.affiliates.length ? `<span class="chip" title="Компании, связанные через общих физлиц">Связанных компаний: ${p.affiliates.length}</span>` : ""}</div>`;
}
function peersBlock(b) {
  if (!b) return "";
  const rows = b.products.filter((r) => r.peers_share >= 0.1 || r.has).slice(0, 6).map((r) => `<div class="peer-row">
      <span>${esc(r.title)}</span><span class="bar"><i style="width:${r.peers_share * 100}%"></i></span>
      <b>${Math.round(r.peers_share * 100)}%</b><span class="${r.has ? "ok" : "muted"}">${r.has ? "✓ есть" : "нет"}</span></div>`).join("");
  return `<div class="sub-h">Похожие компании</div><div class="muted small">${esc(b.description)}: доля с продуктом / у вас</div>${rows}
    ${b.buyer_concentration_vs_peers ? `<div class="muted small">Концентрация покупателей ${b.buyer_concentration_vs_peers}, чем у похожих</div>` : ""}`;
}

// ---------------------------------------------------------------- лента
async function loadFeed() {
  $("#widgetList").innerHTML = `<div class="skeleton"></div>`;
  const { items, quiet } = await api(`/api/feed?${q({ company_id: state.company, role: state.role, limit: 40 })}`);
  state.feed = items; state.feedKey = `${state.company}|${state.role}`; state.rerank = null; state.quiet = quiet;
  $("#navBadge").textContent = items.filter((i) => i.effect === "risk").length || "";
  renderWidget();
  fillFactorFilter();
  renderFeedList();
  renderRadar();
  applyRerank(state.feedKey);
}

function renderWidget() {
  const items = state.feed, quiet = state.quiet;
  $("#widgetList").innerHTML = items.slice(0, 4).map((it) => `
    <div class="w-item" data-ev="${it.event_id}" tabindex="0" role="button">
      <span class="pill ${it.effect}">${EFFECT[it.effect]}</span><span class="muted small">${fmtDate(it.published_at)}</span>
      <div class="title"><b>${esc(it.title)}</b></div>
      <div class="why">Почему вам: ${esc(it.reasons[0]?.why || "")}</div>
      ${it.effective ? `<div>${effectiveTag(it.effective)}</div>` : ""}
    </div>`).join("") || `<div class="muted">Значимых событий для компании нет.</div>`;
  if (quiet) {
    $("#widgetList").insertAdjacentHTML("afterbegin", `<div class="quiet">За последние ${quiet.days} дней значимых для вашей компании событий не было — это тоже ответ: менять планы не нужно.${quiet.last ? ` Последнее важное — ниже.` : ""}</div>`);
  }
  bindCards($("#widgetList"));
}

// ---- фильтры ленты: эффект, фактор, период, поиск, сортировка, «непрочитанные»
const filters = { effect: "all", factor: "", period: "all", text: "", sort: "relevance", unread: false };
function readSet() { try { return new Set(JSON.parse(localStorage.getItem("read:" + state.company) || "[]")); } catch (e) { return new Set(); } }
function markRead(id) {
  const s = readSet(); s.add(id);
  try { localStorage.setItem("read:" + state.company, JSON.stringify([...s].slice(-500))); } catch (e) { /* хранилище недоступно */ }
  document.querySelectorAll(`.feed-item[data-ev="${CSS.escape(id)}"]`).forEach((el) => el.classList.add("read"));
}
function fillFactorFilter() {
  const seen = new Map();
  state.feed.forEach((it) => it.reasons.forEach((r) => { if (!["country", "industry", "region"].includes(r.factor)) seen.set(r.factor, r.factor_title); }));
  $("#fFactor").innerHTML = `<option value="">Все факторы</option>` + [...seen].map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  $("#fFactor").value = seen.has(filters.factor) ? filters.factor : "";
}
function visibleFeed() {
  const read = readSet(), now = Date.now(), days = { week: 7, month: 30 }[filters.period];
  const t = filters.text.toLowerCase();
  let list = state.feed.filter((it) =>
    (filters.effect === "all" || it.effect === filters.effect)
    && (!filters.factor || it.reasons.some((r) => r.factor === filters.factor))
    && (!days || (now - new Date(it.published_at)) / 864e5 <= days)
    && (!t || (it.title + " " + it.summary).toLowerCase().includes(t))
    && (!filters.unread || !read.has(it.event_id)));
  if (filters.sort === "date") list = [...list].sort((a, b) => String(b.published_at).localeCompare(String(a.published_at)));
  return list;
}
function renderFeedList(animate = false) {
  const list = $("#feedList");
  const before = animate ? new Map([...list.children].map((el) => [el.dataset.ev, el.getBoundingClientRect().top])) : null;
  const read = readSet(), notes = (state.rerank && state.rerank.notes) || {};
  const reasonText = (it) => it.reasons.slice(0, 2).map((r) => `${r.factor_title}${r.direction && r.direction !== "unknown" ? " " + DIR[r.direction] : ""}`).join(" · ");
  const items = visibleFeed();
  $("#feedCount").textContent = `${items.length} из ${state.feed.length}`;
  list.innerHTML = items.map((it) => {
    const n = notes[it.event_id];
    return `<div class="feed-item ${read.has(it.event_id) ? "read" : ""} ${it.event_id === state.current ? "active" : ""}" data-ev="${it.event_id}" tabindex="0" role="button">
      <div><span class="pill ${it.effect}">${EFFECT[it.effect]}</span>${it.has_bank_view ? `<span class="pill bank">есть позиция банка</span>` : ""}${effectiveTag(it.effective)}</div>
      <div class="title">${esc(it.title)}</div>
      <div class="meta">${fmtDate(it.published_at)} · ${esc([...new Set(it.sources.map((x) => x.source))].join(", "))}${it.sources.length > 1 ? ` · ${it.sources.length} ${plural(it.sources.length, "публикация", "публикации", "публикаций")}` : ""} · релевантность ${it.score}</div>
      <div class="chips">${entityChips(it)}</div>
      <div class="reasons">${esc(reasonText(it))}</div>
      ${it.reasons.find((r) => r.personal) ? `<div class="personal">⚙ ${esc(it.reasons.find((r) => r.personal).personal.why)}</div>` : ""}
      ${n ? `<div class="ai-note"><b>ИИ:</b> ${esc(n.why)} <span class="rel r${n.relevance}">${"●".repeat(n.relevance)}${"○".repeat(3 - n.relevance)}</span></div>` : ""}
      ${it.story ? `<div class="story-badge">Сюжет · ${it.story.count} ${plural(it.story.count, "событие", "события", "событий")}: ${esc(fmtDate(it.story.timeline[0].published_at))} → ${esc(fmtDate(it.story.timeline[it.story.timeline.length - 1].published_at))}</div>` : ""}
      <div class="score-bar"><i style="width:${Math.min(100, it.score * 60)}%"></i></div>
    </div>`;
  }).join("") || `<div class="empty">${state.feed.length ? "Под фильтры ничего не подошло — сбросьте их." : "Значимых событий нет."}</div>`;
  if (before) for (const el of list.children) {
    const dy = (before.get(el.dataset.ev) ?? el.getBoundingClientRect().top) - el.getBoundingClientRect().top;
    if (dy) el.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 450, easing: "ease-out" });
  }
  bindCards(list);
}
function bindCards(root) {
  root.querySelectorAll("[data-ev]").forEach((el) => {
    const open = () => { show("radar"); openInsight(el.dataset.ev); };
    el.onclick = open;
    el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
  });
}
function bindFeedControls() {
  document.querySelectorAll("[data-feff]").forEach((b) => b.onclick = () => {
    filters.effect = b.dataset.feff;
    document.querySelectorAll("[data-feff]").forEach((x) => x.classList.toggle("on", x === b));
    renderFeedList();
  });
  $("#fFactor").onchange = (e) => { filters.factor = e.target.value; renderFeedList(); };
  $("#fPeriod").onchange = (e) => { filters.period = e.target.value; renderFeedList(); };
  $("#fSort").onchange = (e) => { filters.sort = e.target.value; renderFeedList(); };
  $("#fUnread").onchange = (e) => { filters.unread = e.target.checked; renderFeedList(); };
  let t;
  $("#fText").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { filters.text = e.target.value.trim(); renderFeedList(); }, 150); };
}

// ИИ уточняет порядок верха ленты в фоне: карточки плавно переставляются, у каждой — короткая причина модели
async function applyRerank(key) {
  if (state.llmMode === "mock" || !state.feed.length) return;
  $("#rerankNote").textContent = "ИИ уточняет порядок…";
  let r;
  try { r = await api(`/api/feed/rerank?${q({ company_id: state.company, role: state.role, limit: 40 })}`); } catch (e) { r = { applied: false }; }
  if (key !== state.feedKey) return;  // пользователь уже сменил компанию или роль
  if (!r.applied) { $("#rerankNote").textContent = ""; return; }
  state.rerank = r;
  const pos = new Map(r.order.map((id, i) => [id, i]));
  state.feed.sort((a, b) => (pos.get(a.event_id) ?? 1e9) - (pos.get(b.event_id) ?? 1e9));
  renderFeedList(true);
  renderWidget();
  $("#rerankNote").innerHTML = `✨ Порядок уточнён ИИ <span class="muted">(${esc(r.model || "")}${r.cached ? ", из кэша" : ""})</span>`;
}

// Радар на главной: чувствительность компании и сигналы ленты
function renderRadar() {
  const box = document.getElementById("radarChart");
  if (!box || !state.exposures) return;
  box.innerHTML = factorRadar(state.exposures, state.feed) || `<div class="muted small">Недостаточно данных о компании.</div>`;
}

// ---------------------------------------------------------------- инсайт
async function openInsight(eventId, refresh = false) {
  state.current = eventId;
  markRead(eventId);
  document.querySelectorAll(".feed-item").forEach((el) => el.classList.toggle("active", el.dataset.ev === eventId));
  $("#insightPanel").innerHTML = `<div class="insight"><div id="previewBox"></div><div class="eyebrow">Агент анализирует событие</div>
    <ol class="progress" id="agentProgress"></ol></div>`;
  const prog = () => document.getElementById("agentProgress");
  try {
    await sse(`/api/insights/${eventId}/stream?${q({ company_id: state.company, role: state.role, refresh })}`, {}, (e) => {
      if (state.current !== eventId || !prog()) return;
      if (e.type === "step_start") {
        prog().insertAdjacentHTML("beforeend", `<li class="run" data-step="${esc(e.name)}">${esc(stepTitle(e.name))}<span class="muted small"></span></li>`);
      } else if (e.type === "step_end") {
        const li = [...prog().querySelectorAll("li.run")].reverse().find((x) => x.dataset.step === e.name);
        if (li) { li.className = e.fallback ? "fallback" : "ok"; li.querySelector("span").textContent = ` · ${e.model ? e.model + " · " : ""}${e.duration_ms} мс${e.fallback ? " · без LLM" : ""}`; }
      } else if (e.type === "preview") {
        // главный вывод сразу, полный разбор догружается
        const p = e.insight;
        $("#previewBox").innerHTML = `<span class="pill ${p.effect}">${EFFECT[p.effect]}</span> <span class="muted small">быстрый вывод по профилю · полный разбор готовится…</span>
          <h2 style="margin-top:8px">${esc(p.headline)}</h2>
          <div class="takeaway"><span class="claim-kind small" style="color:var(--ai)">ВЫВОД СЕРВИСА · </span>${esc(p.takeaway.text)}</div>
          ${scenarioCards(p.scenarios)}`;
      } else if (e.type === "done") {
        state.insight = e.insight; renderInsight("analysis");
      } else if (e.type === "error") { throw new Error(e.detail); }
    });
  } catch (e) { $("#insightPanel").innerHTML = `<div class="insight bad">Ошибка: ${esc(e.message)}</div>`; }
}

function srcTags(ids) { return (ids || []).map((s) => `<span class="src" data-src="${esc(s)}">${esc(s.split(":")[0] === "bank" ? "банк " + s.split(":")[1] : s.split(":")[0] === "news" ? "новость" : s)}</span>`).join(""); }
function claims(list) {
  return (list || []).map((c) => `<div class="claim ${c.kind} ${c.confidence === "low" ? "conf-low" : ""}">
    <span class="kind">${KIND[c.kind] || c.kind}</span>${esc(c.text)}<div>${srcTags(c.sources)}${c.kind !== "fact" ? `<span class="conf ${c.confidence}" title="Уверенность вывода">${CONF[c.confidence] || ""}</span>` : ""}</div></div>`).join("") || `<div class="muted small">нет данных</div>`;
}

const SCEN_CLASS = { base: "watch", negative: "risk", positive: "opportunity" };
function scenarioCards(list) {
  if (!list || !list.length) return "";
  const est = list[0].estimated || [];
  return `<div class="scenarios">${list.map((s) => `<div class="scen ${SCEN_CLASS[s.name]}">
      <div class="scen-t">${esc(s.title)}</div>
      <div class="scen-v ${s.total_rub < 0 ? "bad" : s.total_rub > 0 ? "ok" : ""}">${rub(s.total_rub)}<span class="muted small"> в год</span></div>
      ${s.share_of_revenue != null ? `<div class="muted small">${(s.share_of_revenue * 100).toFixed(2).replace(".", ",")}% выручки</div>` : ""}
      <div class="small">${esc(s.assumptions)}</div>
      ${s.name === "base" ? `<div>${srcTags(s.sources)}</div>` : ""}</div>`).join("")}</div>
    ${est.length ? `<div class="muted small">Оценка по профилю: ${esc(est.join("; "))}. Свои цифры — в блоке «Что если».</div>` : ""}`;
}
function rub(v) {
  if (v == null) return "—";
  const a = Math.abs(v), sign = v < 0 ? "−" : v > 0 ? "+" : "";
  const n = (x) => x.toFixed(1).replace(".", ",");
  return sign + (a >= 1e9 ? n(a / 1e9) + " млрд ₽" : a >= 1e6 ? n(a / 1e6) + " млн ₽" : Math.round(a / 1e3) + " тыс. ₽");
}

// «Что если»: слайдеры шоков и свои суммы → пересчёт на сервере тем же калькулятором, что у агента
function whatIfBlock() {
  return `<details class="whatif" id="whatIf"><summary>Что если — посчитать для своих цифр</summary><div id="whatIfBody" class="muted small">Загрузка…</div></details>`;
}
const WI = { debt_rub: ["Долг, млн ₽", 1e6], floating_share: ["Доля под плавающей ставкой, %", 0.01], import_rub: ["Импорт в год, млн ₽", 1e6], payroll_rub: ["ФОТ в год, млн ₽", 1e6] };
async function initWhatIf() {
  const box = document.getElementById("whatIfBody");
  if (!box) return;
  const r = await api("/api/scenario", { method: "POST", body: JSON.stringify({ company_id: state.company, event_id: state.current }) });
  const b = r.baseline, d = r.defaults;
  const slider = (id, label, min, max, step, val, hint) => `<label class="wi-row"><span>${label}</span>
    <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"><b id="${id}V"></b><span class="muted small">${hint}</span></label>`;
  const field = (k) => `<label class="wi-field">${WI[k][0]}<input type="number" min="0" step="any" id="wi_${k}" value="${d[k] != null ? +(d[k] / WI[k][1]).toFixed(2) : ""}" placeholder="нет данных"></label>`;
  box.className = "";
  box.innerHTML = `
    <div class="wi-grid">
      ${slider("wiRate", "Ключевая ставка, изменение", -4, 4, 0.25, b.rate_delta_pp, `прогноз банка: ${b.rate_delta_pp > 0 ? "+" : ""}${String(b.rate_delta_pp).replace(".", ",")} п.п. (средняя ${String(b.key_rate_avg_next).replace(".", ",")}% против ${String(b.key_rate_now).replace(".", ",")}% сейчас)`)}
      ${slider("wiFx", "Курс рубля: ослабление, %", -20, 40, 0.1, b.fx_change_pct, `прогноз банка: ${String(b.fx_change_pct).replace(".", ",")}% (${b.usd_rub.join(" → ")} ₽/$)`)}
      ${slider("wiWage", "Рост зарплат за год, %", 0, 25, 0.1, b.wage_growth_pct, `прогноз банка: ${String(b.wage_growth_pct).replace(".", ",")}% — заложен в бюджет как база`)}
    </div>
    <div class="wi-fields">${Object.keys(WI).map(field).join("")}</div>
    <div id="wiOut" class="wi-out"></div>
    <div class="muted small">${r.estimated.length ? "Значения по умолчанию — оценки по профилю: " + esc(r.estimated.join("; ")) + ". Замените своими — расчёт мгновенно обновится." : ""}</div>`;
  let timer;
  const run = () => { clearTimeout(timer); timer = setTimeout(recalc, 120); };
  box.querySelectorAll("input").forEach((el) => el.addEventListener("input", run));
  recalc();
}
async function recalc() {
  const v = (id) => +document.getElementById(id).value;
  $("#wiRateV").textContent = `${v("wiRate") > 0 ? "+" : ""}${String(v("wiRate")).replace(".", ",")} п.п.`;
  $("#wiFxV").textContent = `${v("wiFx") > 0 ? "+" : ""}${String(v("wiFx")).replace(".", ",")}%`;
  $("#wiWageV").textContent = `${String(v("wiWage")).replace(".", ",")}%`;
  const inputs = { rate_delta_pp: v("wiRate"), fx_change_pct: v("wiFx"), wage_growth_pct: v("wiWage") };
  for (const k of Object.keys(WI)) { const el = document.getElementById("wi_" + k); if (el.value !== "") inputs[k] = +el.value * WI[k][1]; }
  try {
    const { result: r } = await api("/api/scenario", { method: "POST", body: JSON.stringify({ company_id: state.company, event_id: state.current, inputs }) });
    $("#wiOut").innerHTML = `<div class="wi-total ${r.total_rub < 0 ? "bad" : r.total_rub > 0 ? "ok" : ""}">${rub(r.total_rub)} <span class="muted small">в год к плану${r.share_of_revenue != null ? ` · ${(r.share_of_revenue * 100).toFixed(2).replace(".", ",")}% выручки` : ""}</span></div>
      ${r.rows.map((x) => `<div class="wi-line"><span>${esc(x.title)}</span><b class="${x.effect_rub < 0 ? "bad" : x.effect_rub > 0 ? "ok" : ""}">${rub(x.effect_rub)}</b><span class="muted small">${esc(x.formula)}</span></div>`).join("")}`;
  } catch (e) { $("#wiOut").textContent = "Не удалось посчитать: " + e.message; }
}

function renderInsight(tab) {
  const i = state.insight;
  const allOk = i.quality.every((c) => c.passed);
  const tabs = [["analysis", "Интерпретация"], ["sources", `Источники (${i.sources.length})`], ["trace", "Как получен вывод"]];
  let body = "";
  if (tab === "analysis") {
    body = `
      <details class="impact-wrap" open><summary>Карта влияния: как событие доходит до вашей компании</summary>${impactMap(i)}</details>
      <div class="kinds"><span class="k-fact">Факт</span><span class="k-assumption">Предположение источника</span><span class="k-bank_view">Позиция Альфа-Банка</span><span class="k-ai_inference">Вывод сервиса (AI)</span></div>
      <div class="q"><h4><span class="n">1</span>Что произошло</h4>${claims(i.what_happened)}</div>
      <div class="q"><h4><span class="n">2</span>Почему это важно для вашей компании</h4>${claims(i.why_it_matters)}</div>
      <div class="q"><h4><span class="n">3</span>Возможные последствия</h4>
        <div class="cols"><div><b class="bad small">Риски</b>${claims(i.consequences.risks)}</div><div><b class="ok small">Возможности</b>${claims(i.consequences.opportunities)}</div></div>
        <b class="small muted">Что повлияет на развитие ситуации</b>${claims(i.consequences.drivers)}
        ${(i.scenarios || []).length ? `<div class="sub-h">Сценарии на 12 месяцев <span class="muted small">— суммы считает калькулятор, а не модель</span></div>${scenarioCards(i.scenarios)}` : ""}
        ${(i.what_could_change || []).length ? `<div class="sub-h">Что может изменить вывод</div>${claims(i.what_could_change)}` : ""}
        ${whatIfBlock()}</div>
      <div class="q"><h4><span class="n">4</span>Что можно сделать</h4>${claims(i.actions)}
        ${i.products.map((p) => `<div class="product"><span class="lbl">${esc(p.label)}</span><br><b>${esc(p.product)}</b> — ${esc(p.why)}</div>`).join("")}</div>
      <div class="q"><h4><span class="n">5</span>Как это оценивает Альфа-Банк</h4>${claims(i.bank_view)}</div>
      <div class="q"><h4><span class="n">6</span>На чём основан вывод</h4>
        <div class="small muted">Каждое утверждение выше помечено типом и ссылками. Самопроверка (${i.iterations} итер.):</div>
        <ul class="quality">${i.quality.map((c) => `<li class="${c.passed ? "ok" : "bad"}">${c.passed ? "✓" : "✗"} ${esc(c.name)}${c.passed ? "" : " — " + esc(c.detail)}</li>`).join("")}</ul>
        ${(i.relevance.consistency_issues || []).map((x) => `<div class="notice">${esc(x)}</div>`).join("")}
      </div>
      <div class="feedback">Полезно? <button class="btn ghost small" data-fb="1">👍</button><button class="btn ghost small" data-fb="-1">👎</button>
        <span id="fbReasons" hidden>Что не так?
          <button class="btn ghost small" data-fbr="not_relevant">Не про мой бизнес</button>
          <button class="btn ghost small" data-fbr="known">Уже знал(а)</button>
          <button class="btn ghost small" data-fbr="inaccurate">Неточный вывод</button></span><span id="fbDone"></span></div>
      ${chatBlock()}`;
  } else if (tab === "sources") {
    body = `<ol class="sources-list">${i.sources.map((s) => `<li id="src-${CSS.escape(s.id)}"><b>[${esc(s.type === "bank" ? "Альфа-Банк" : s.type === "news" ? "Новость" : s.type === "graph" ? "Граф платежей" : s.type === "calc" ? "Расчёт" : s.type === "peers" ? "Похожие компании" : "Профиль")}]</b> ${esc(s.title)}
      ${s.date ? `<span class="muted"> · ${fmtDate(s.date)}</span>` : ""}${s.page ? `<span class="muted"> · стр. ${s.page}</span>` : ""}
      ${s.stale ? `<span class="pill mixed">материал устарел</span>` : ""}
      ${s.url ? ` <a href="${esc(s.url)}" target="_blank" rel="noopener">первоисточник ↗</a>` : ""}
      ${s.quote ? `<div class="muted small">«${esc(s.quote)}»</div>` : ""}<div class="muted small">${esc(s.id)}</div></li>`).join("")}</ol>`;
  } else {
    body = `<div id="traceBox" class="loading">Загрузка трассировки…</div>`;
    loadTrace(i.trace_id);
  }
  $("#insightPanel").innerHTML = `<div class="insight">
    <div><span class="pill ${i.effect}">${EFFECT[i.effect]}</span>
      <span class="muted small">генерация: ${esc(i.generated_by)} · trace ${esc(i.trace_id)} · ${allOk ? "<span class='ok'>самопроверка пройдена</span>" : "<span class='bad'>есть замечания</span>"}</span>
      <button class="btn ghost small" id="refreshBtn" style="float:right">Пересчитать</button></div>
    <h2 style="margin-top:8px">${esc(i.headline)}</h2>
    <div class="takeaway"><span class="claim-kind small" style="color:var(--ai)">ВЫВОД СЕРВИСА · </span>${esc(i.takeaway.text)}<div>${srcTags(i.takeaway.sources)}</div></div>
    ${i.data_gaps.length ? `<div class="notice">Учтены пробелы в данных: ${esc(i.data_gaps.join("; "))}</div>` : ""}
    ${(() => { const f = state.feed.find((x) => x.event_id === state.current); return f && f.story ? `<details class="story" open><summary>Развитие сюжета · ${f.story.count} ${plural(f.story.count, "событие", "события", "событий")}</summary>${storyLine(f.story, state.current)}</details>` : ""; })()}
    <div class="tabs" role="tablist">${tabs.map(([k, t]) => `<button class="tab ${k === tab ? "active" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${t}</button>`).join("")}</div>
    ${body}</div>`;
  document.querySelectorAll("[data-tab]").forEach((el) => el.onclick = () => renderInsight(el.dataset.tab));
  bindImpactMap($("#insightPanel"));
  const wi = document.getElementById("whatIf");
  if (wi) wi.addEventListener("toggle", () => { if (wi.open && !wi.dataset.ready) { wi.dataset.ready = "1"; initWhatIf(); } });
  document.querySelectorAll("[data-story-ev]").forEach((el) => el.onclick = () => { if (el.dataset.storyEv !== state.current) openInsight(el.dataset.storyEv); });
  document.querySelectorAll("[data-src]").forEach((el) => el.onclick = () => { renderInsight("sources"); const li = document.getElementById("src-" + CSS.escape(el.dataset.src)); if (li) { li.classList.add("hl"); li.scrollIntoView({ block: "center" }); } });
  $("#refreshBtn").onclick = () => openInsight(state.current, true);
  document.querySelectorAll("[data-fb]").forEach((el) => el.onclick = () => +el.dataset.fb > 0 ? sendFeedback(1) : ($("#fbReasons").hidden = false));
  document.querySelectorAll("[data-fbr]").forEach((el) => el.onclick = () => sendFeedback(-1, el.dataset.fbr));
  bindChat();
}

async function loadTrace(traceId) {
  const { steps } = await api(`/api/trace/${traceId}`);
  $("#traceBox").classList.remove("loading");
  $("#traceBox").innerHTML = traceTimeline(steps) + `<div class="sub-h">Шаги подробно</div>` + steps.map((s) => `<div class="trace-step">
    <div><span class="k">${esc(s.kind)}</span> <b>${s.step_no}. ${esc(s.name)}</b>
    <span class="muted small">${s.model ? "· " + esc(s.model) : ""} · ${s.duration_ms} мс${s.tokens_in ? ` · ${s.tokens_in}/${s.tokens_out} ток. · ${(+s.cost_rub).toFixed(3)} ₽` : ""}</span></div>
    <details><summary class="small muted">вход/выход</summary><pre>${esc(JSON.stringify({ input: s.input, output: s.output }, null, 2))}</pre></details></div>`).join("");
}

// ---------------------------------------------------------------- чат
function chatBlock() {
  return `<div class="chat"><h4>Задать уточняющий вопрос</h4>
    <div class="quick">${["Какие главные риски?", "Что говорит банк?", "Покажи разрез по контрагентам", "Что делать в первую очередь?", "Откуда эти выводы?"].map((t) => `<button data-q="${t}">${t}</button>`).join("")}</div>
    <div class="chat-log" id="chatLog"></div>
    <div class="chat-row"><input id="chatInput" placeholder="Например: как это повлияет на наш лизинг?"><button class="btn" id="chatSend">Спросить</button></div></div>`;
}
function bindChat() {
  if (!$("#chatSend")) return;
  const send = async (text) => {
    if (!text) return;
    $("#chatLog").insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div>`);
    $("#chatInput").value = "";
    $("#chatLog").insertAdjacentHTML("beforeend", `<div class="msg bot typing"></div>`);
    const bubble = [...document.querySelectorAll("#chatLog .msg.bot")].pop();
    try {
      await sse("/api/chat/stream", { method: "POST", body: JSON.stringify({ event_id: state.current, company_id: state.company, role: state.role, question: text }) }, (e) => {
        if (e.type === "delta") { bubble.classList.remove("typing"); bubble.textContent += e.text; }
        if (e.type === "meta") bubble.title = "режим: " + e.mode;
      });
    } catch (e) { bubble.classList.add("bad"); bubble.textContent = e.message; }
  };
  $("#chatSend").onclick = () => send($("#chatInput").value.trim());
  $("#chatInput").onkeydown = (e) => { if (e.key === "Enter") send($("#chatInput").value.trim()); };
  document.querySelectorAll("[data-q]").forEach((b) => b.onclick = () => send(b.dataset.q));
}

async function sendFeedback(rating, reason = null) {
  const r = await api("/api/feedback", { method: "POST", body: JSON.stringify({ event_id: state.current, company_id: state.company, role: state.role, rating, reason }) });
  $("#fbReasons").hidden = true;
  const names = (r.changed || []).map((c) => `«${c.title}»`).join(", ");
  $("#fbDone").textContent = names ? (rating < 0 ? `Спасибо! Новости про ${names} будут ниже в ленте.` : `Спасибо! Новости про ${names} будут выше.`) : "Спасибо, учтём!";
  if (names) { toast("Лента обновлена с учётом вашей оценки — изменить можно в профиле"); loadProfile(); loadFeed(); }
}

// ---------------------------------------------------------------- проверка новой новости
async function analyzeNews(e) {
  e.preventDefault();
  const url = $("#analyzeUrl").value.trim(), text = $("#analyzeText").value.trim();
  if (!url && !text) return;
  $("#analyzeDialog").close();
  show("radar");
  $("#insightPanel").innerHTML = `<div class="insight loading">Классификация и анализ новости…</div>`;
  try {
    const r = await api("/api/news/analyze", { method: "POST", body: JSON.stringify({ url: url || null, text: text || null, company_id: state.company, role: state.role }) });
    state.current = r.event_id; state.insight = r.insight;
    renderInsight("analysis");
    loadFeed();
  } catch (err) { $("#insightPanel").innerHTML = `<div class="insight bad">Ошибка: ${esc(err.message)}</div>`; }
  $("#analyzeUrl").value = ""; $("#analyzeText").value = "";
}

// ---------------------------------------------------------------- дайджест под роль
async function loadDigest() {
  const box = $("#digestBox");
  box.innerHTML = `<div class="skeleton"></div>`;
  const d = await api(`/api/digest?${q({ company_id: state.company, role: state.role, period: state.digestPeriod || "week" })}`);
  const per = d.period === "day" ? "за день" : "за неделю";
  const item = (t) => `<li class="dg-item" data-ev="${t.event_id}" tabindex="0" role="button"><span class="pill ${t.effect}">${EFFECT[t.effect]}</span> <b>${esc(t.title)}</b>
    <div class="muted small">${fmtDate(t.published_at)} · ${esc(t.why)}</div></li>`;
  const counts = Object.entries(d.counts).filter(([, v]) => v).map(([k, v]) => `${v} — ${EFFECT[k]}`).join(", ");
  const lead = d.quiet ? `За период значимых для компании событий не было — это тоже ответ: менять планы не нужно.`
    : `${counts || "Есть события для отслеживания"}. Фокус для роли «${d.role_title}»: ${d.focus}.`;
  box.innerHTML = `
    <div class="card dg-main">
      <div class="eyebrow">${esc(d.role_title)} · ${per}</div>
      <h3>${esc(d.company)}</h3>
      <p>${esc(lead)}</p>
      <div class="sub-h">Главное</div><ul class="dg-list">${d.top.map(item).join("") || `<li class="muted">Нет событий</li>`}</ul>
      ${d.dates.length ? `<div class="sub-h">Сроки</div><ul class="dg-list">${d.dates.map((x) => `<li data-ev="${x.event_id}" tabindex="0" role="button">⏱ <b>${fmtDate(x.date)}</b> — через ${x.days} ${plural(x.days, "день", "дня", "дней")}: ${esc(x.title)}</li>`).join("")}</ul>` : ""}
      ${d.stories.length ? `<div class="sub-h">Сюжеты, которые развивались</div><ul class="dg-list">${d.stories.map((x) => `<li>${esc(x.title)} <span class="muted small">· ${x.count} ${plural(x.count, "событие", "события", "событий")}</span></li>`).join("")}</ul>` : ""}
      ${d.bank_revisions.length ? `<div class="sub-h">Альфа-Банк пересмотрел прогнозы</div><ul class="dg-list">${d.bank_revisions.map((x) => `<li>${esc(x.text)}</li>`).join("")}</ul>` : ""}
    </div>
    <div class="dg-side">
      <div class="card email">
        <div class="muted small">Предпросмотр письма</div>
        <div class="em-head"><div><b>Альфа-Бизнес · Бизнес-радар</b></div><div class="muted small">Тема: ${esc(d.quiet ? `Спокойная ${d.period === "day" ? "день" : "неделя"} для вашего бизнеса` : `${d.top.length} ${plural(d.top.length, "событие", "события", "событий")} ${per}: ${d.top[0] ? d.top[0].title.slice(0, 60) : ""}`)}</div></div>
        <div class="em-body"><p>Здравствуйте! Подборка ${per} для роли «${esc(d.role_title)}».</p>
          <ol>${d.top.slice(0, 3).map((t) => `<li><b>${esc(t.title)}</b><br><span class="muted">${esc(t.why)}</span></li>`).join("")}</ol>
          <p><span class="em-btn">Открыть Бизнес-радар</span></p>
          <p class="muted small">Это демонстрация: письмо не отправляется.</p></div>
      </div>
      <div class="card push"><div class="muted small">Предпросмотр push</div>
        <div class="push-n"><b>Альфа-Бизнес</b><div>${esc(d.top[0] ? `${EFFECT[d.top[0].effect][0].toUpperCase() + EFFECT[d.top[0].effect].slice(1)}: ${d.top[0].title}` : "Важных событий нет")}</div></div></div>
    </div>`;
  bindCards(box);
}

// ---------------------------------------------------------------- тема
function applyTheme(t) { document.documentElement.dataset.theme = t; }
function toggleTheme() {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("theme", next); } catch (e) { /* хранилище недоступно */ }
}
try { const t = localStorage.getItem("theme"); if (t) applyTheme(t); } catch (e) { /* хранилище недоступно */ }

// ---------------------------------------------------------------- метрики
async function loadMetrics() {
  const m = await api("/api/metrics");
  const card = (t, v, s = "") => `<div class="card metric"><div class="muted small">${t}</div><div class="v">${v}</div><div class="muted small">${s}</div></div>`;
  $("#metricsBox").innerHTML = [
    card("Режим LLM", m.llm.mode, { live: "вызовы разрешённых моделей", replay: "ответы моделей из снапшота, без ключа", mock: "без LLM" }[m.llm.mode] || ""),
    card("Потрачено сегодня (МСК)", `${m.llm.spent_rub} ₽`, `лимит ${m.llm.budget_rub} ₽ · резерв для пользователей после ${m.llm.background_limit_rub} ₽`),
    card("Инсайтов построено", m.insights.total),
    card("Прошли самопроверку", m.insights.quality_passed_share != null ? Math.round(m.insights.quality_passed_share * 100) + "%" : "—"),
    card("Обратная связь", `👍 ${m.feedback.pos} · 👎 ${m.feedback.neg}`),
    card("Оценка LLM-судьи (1–5)", m.insights.judge && m.insights.judge.avg != null ? String(m.insights.judge.avg).replace(".", ",") : "—",
      m.insights.judge && m.insights.judge.n ? `${m.insights.judge.n} разборов · ` + Object.entries(m.insights.judge.by_axis).map(([k, v]) => `${({ personalization: "персонализация", depth: "глубина", grounding: "обоснованность", bank_view: "позиция банка", actionability: "конкретность" })[k] || k} ${String(v).replace(".", ",")}`).join(" · ") : "появится после разборов моделью"),
    card("События / компании", `${m.events} / ${m.companies}`),
  ].join("");
}

// ---------------------------------------------------------------- источники новостей
const SRC_STATUS = { ok: ["работает", "ok"], degraded: ["сбои", "mid"], down: ["недоступен", "bad"], never: ["ещё не опрашивался", "muted"], disabled: ["выключен", "muted"] };
function ago(s) {
  if (!s) return "—";
  const m = Math.round((Date.now() - new Date(s)) / 60000);
  return m < 1 ? "только что" : m < 60 ? `${m} мин назад` : m < 1440 ? `${Math.round(m / 60)} ч назад` : fmtDate(s);
}
async function loadSources() {
  const h = await api("/api/sources/health");
  const li = h.last_ingest;
  $("#sourcesHead").innerHTML = `Сбор каждые ${h.schedule_minutes} мин${li ? ` · последний проход ${ago(li.at)}: получено ${li.fetched}, новых событий ${li.new_events}, склеено с уже известными ${li.merged}, отсеяно как шум ${li.noise}` : ""}.
    Новости вне бизнес-повестки отсекаются до модели; модель разбирает только новости с бизнес-факторами и официальные сообщения.`;
  $("#sourcesBox").innerHTML = `<div class="table-wrap"><table class="sources"><thead><tr><th>Источник</th><th>Доверие</th><th>Статус</th><th>Последний опрос</th><th title="получено / новых событий / склеено / шум">Последний проход</th><th>Всего событий</th></tr></thead><tbody>
    ${h.sources.map((s) => { const st = SRC_STATUS[s.status] || [s.status, ""]; const t = TRUST[s.trust] || TRUST.media; return `<tr>
      <td><b>${esc(s.title)}</b><div class="muted small">${s.type === "rss" ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">RSS ↗</a>` : esc(s.type)}</div></td>
      <td><span class="chip trust ${esc(s.trust)}" title="${esc(t[1])}">${esc(t[0])}</span></td>
      <td><span class="status ${st[1]}">● ${st[0]}</span>${s.last_error ? `<div class="muted small err" title="${esc(s.last_error)}">${esc(s.last_error.slice(0, 80))}</div>` : ""}</td>
      <td>${ago(s.last_run)}${s.latency_ms != null ? `<div class="muted small">${s.latency_ms} мс</div>` : ""}</td>
      <td>${s.fetched != null ? `${s.fetched} / ${s.new_events} / ${s.merged} / ${s.noise}` : "—"}</td>
      <td>${s.total_new_events ?? "—"}</td></tr>`; }).join("")}</tbody></table></div>
    <div class="muted small" style="margin-top:8px">Последний проход: получено / новых событий / склеено с известными / отсеяно как шум.</div>`;
}

// ---------------------------------------------------------------- бюджет ИИ (временный индикатор)
async function loadBudget() {
  try {
    const b = await api("/api/llm/budget");
    state.llmMode = b.mode;
    const el = $("#budgetBadge");
    if (b.mode !== "live") { el.textContent = `ИИ: ${b.mode === "replay" ? "сохранённые ответы, без ключа" : "выключен"}`; el.className = "budget-badge off"; return; }
    const share = b.budget_rub ? b.left_rub / b.budget_rub : 0;
    el.className = "budget-badge " + (share < 0.2 ? "low" : share < 0.5 ? "mid" : "ok");
    el.innerHTML = `ИИ: осталось <b>${b.left_rub.toFixed(0)} ₽</b> из ${b.budget_rub.toFixed(0)}`;
    el.title = `Потрачено ${b.spent_rub} ₽ (${b.source === "provider" ? "по данным провайдера — общий расход команды" : "по учёту этого сервера"}); этот сервер: ${b.spent_this_server_rub} ₽. Сброс в 03:00 МСК.`;
  } catch (e) { /* индикатор необязателен */ }
}
loadBudget(); setInterval(loadBudget, 60000);

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="notice">Ошибка запуска: ${esc(e.message)}</div>`); });
