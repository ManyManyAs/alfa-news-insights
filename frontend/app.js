// Прототип интерфейса «Бизнес-радар» в интернет-банке. Без сборки и зависимостей.
const state = { users: [], roles: {}, user: null, company: null, role: null, feed: [], current: null, insight: null };
const $ = (s) => document.querySelector(s);
const EFFECT = { risk: "риск", opportunity: "возможность", mixed: "риски и возможности", watch: "отслеживать" };
const KIND = { fact: "Факт", assumption: "Предположение источника", bank_view: "Позиция Альфа-Банка", ai_inference: "Вывод сервиса (AI)" };
const DIR = { up: "↑", down: "↓", stable: "→", unknown: "·" };

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
  "repair:rules": "Удаление неподтверждённого", revise: "Правка по замечаниям" };
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
  $("#analyzeSubmit").onclick = analyzeNews;
  if (users.length) await selectUser(users[0].user_id);
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
}

async function reload() {
  state.insight = null; state.current = null;
  $("#insightPanel").innerHTML = `<div class="empty">Выберите событие — сервис объяснит, что оно значит именно для вашей компании.</div>`;
  await Promise.all([loadProfile(), loadFeed()]);
}

// ---------------------------------------------------------------- профиль
async function loadProfile() {
  const { profile: p, exposures } = await api(`/api/companies/${state.company}?${q({ role: state.role })}`);
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
    <div class="muted small" style="margin-top:10px">Чувствительность к факторам:</div>
    <div class="chips">${exposures.slice(0, 6).map((e) => `<span class="chip" title="${esc(e.reasons.map((r) => r.why).join("; "))}">${esc(e.title)} · ${e.weight}</span>`).join("")}</div>
    ${p.data_gaps.length ? `<div class="notice">Неполные данные: ${esc(p.data_gaps.join("; "))}</div>` : ""}
    <div class="muted small">* оценка по выборке клиентов с тем же ОКВЭД</div>`;
}

// ---------------------------------------------------------------- лента
async function loadFeed() {
  $("#widgetList").innerHTML = `<div class="skeleton"></div>`;
  const { items } = await api(`/api/feed?${q({ company_id: state.company, role: state.role })}`);
  state.feed = items;
  $("#navBadge").textContent = items.filter((i) => i.effect === "risk").length || "";
  const reasonText = (it) => it.reasons.slice(0, 2).map((r) => `${r.factor_title} ${DIR[r.direction] || ""}`).join(" · ");
  $("#widgetList").innerHTML = items.slice(0, 4).map((it) => `
    <div class="w-item" data-ev="${it.event_id}">
      <span class="pill ${it.effect}">${EFFECT[it.effect]}</span><span class="muted small">${fmtDate(it.published_at)}</span>
      <div class="title"><b>${esc(it.title)}</b></div>
      <div class="why">Почему вам: ${esc(it.reasons[0]?.why || "")}</div>
    </div>`).join("") || `<div class="muted">Значимых событий для компании нет.</div>`;
  $("#feedList").innerHTML = items.map((it) => `
    <div class="feed-item" data-ev="${it.event_id}">
      <div><span class="pill ${it.effect}">${EFFECT[it.effect]}</span>${it.has_bank_view ? `<span class="pill bank">есть позиция банка</span>` : ""}</div>
      <div class="title">${esc(it.title)}</div>
      <div class="meta">${fmtDate(it.published_at)} · ${esc(it.sources.map((s) => s.source).join(", "))} · релевантность ${it.score}</div>
      <div class="reasons">${esc(reasonText(it))}</div>
      <div class="score-bar"><i style="width:${Math.min(100, it.score * 60)}%"></i></div>
    </div>`).join("") || `<div class="empty">Значимых событий нет.</div>`;
  document.querySelectorAll("[data-ev]").forEach((el) => el.onclick = () => { show("radar"); openInsight(el.dataset.ev); });
}

// ---------------------------------------------------------------- инсайт
async function openInsight(eventId, refresh = false) {
  state.current = eventId;
  document.querySelectorAll(".feed-item").forEach((el) => el.classList.toggle("active", el.dataset.ev === eventId));
  $("#insightPanel").innerHTML = `<div class="insight"><div class="eyebrow">Агент анализирует событие</div>
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
      } else if (e.type === "done") {
        state.insight = e.insight; renderInsight("analysis");
      } else if (e.type === "error") { throw new Error(e.detail); }
    });
  } catch (e) { $("#insightPanel").innerHTML = `<div class="insight bad">Ошибка: ${esc(e.message)}</div>`; }
}

function srcTags(ids) { return (ids || []).map((s) => `<span class="src" data-src="${esc(s)}">${esc(s.split(":")[0] === "bank" ? "банк " + s.split(":")[1] : s.split(":")[0] === "news" ? "новость" : s)}</span>`).join(""); }
function claims(list) {
  return (list || []).map((c) => `<div class="claim ${c.kind} ${c.confidence === "low" ? "conf-low" : ""}">
    <span class="kind">${KIND[c.kind] || c.kind}</span>${esc(c.text)}<div>${srcTags(c.sources)}</div></div>`).join("") || `<div class="muted small">нет данных</div>`;
}

function renderInsight(tab) {
  const i = state.insight;
  const allOk = i.quality.every((c) => c.passed);
  const tabs = [["analysis", "Интерпретация"], ["sources", `Источники (${i.sources.length})`], ["trace", "Как получен вывод"]];
  let body = "";
  if (tab === "analysis") {
    body = `
      <div class="q"><h4><span class="n">1</span>Что произошло</h4>${claims(i.what_happened)}</div>
      <div class="q"><h4><span class="n">2</span>Почему это важно для вашей компании</h4>${claims(i.why_it_matters)}</div>
      <div class="q"><h4><span class="n">3</span>Возможные последствия</h4>
        <div class="cols"><div><b class="bad small">Риски</b>${claims(i.consequences.risks)}</div><div><b class="ok small">Возможности</b>${claims(i.consequences.opportunities)}</div></div>
        <b class="small muted">Что повлияет на развитие ситуации</b>${claims(i.consequences.drivers)}</div>
      <div class="q"><h4><span class="n">4</span>Что можно сделать</h4>${claims(i.actions)}
        ${i.products.map((p) => `<div class="product"><span class="lbl">${esc(p.label)}</span><br><b>${esc(p.product)}</b> — ${esc(p.why)}</div>`).join("")}</div>
      <div class="q"><h4><span class="n">5</span>Как это оценивает Альфа-Банк</h4>${claims(i.bank_view)}</div>
      <div class="q"><h4><span class="n">6</span>На чём основан вывод</h4>
        <div class="small muted">Каждое утверждение выше помечено типом и ссылками. Самопроверка (${i.iterations} итер.):</div>
        <ul class="quality">${i.quality.map((c) => `<li class="${c.passed ? "ok" : "bad"}">${c.passed ? "✓" : "✗"} ${esc(c.name)}${c.passed ? "" : " — " + esc(c.detail)}</li>`).join("")}</ul>
        ${(i.relevance.consistency_issues || []).map((x) => `<div class="notice">${esc(x)}</div>`).join("")}
      </div>
      <div class="feedback">Полезно? <button class="btn ghost small" data-fb="1">👍</button><button class="btn ghost small" data-fb="-1">👎</button><span id="fbDone"></span></div>
      ${chatBlock()}`;
  } else if (tab === "sources") {
    body = `<ol class="sources-list">${i.sources.map((s) => `<li id="src-${CSS.escape(s.id)}"><b>[${esc(s.type === "bank" ? "Альфа-Банк" : s.type === "news" ? "Новость" : s.type === "graph" ? "Граф платежей" : "Профиль")}]</b> ${esc(s.title)}
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
    <div class="tabs">${tabs.map(([k, t]) => `<div class="tab ${k === tab ? "active" : ""}" data-tab="${k}">${t}</div>`).join("")}</div>
    ${body}</div>`;
  document.querySelectorAll("[data-tab]").forEach((el) => el.onclick = () => renderInsight(el.dataset.tab));
  document.querySelectorAll("[data-src]").forEach((el) => el.onclick = () => { renderInsight("sources"); const li = document.getElementById("src-" + CSS.escape(el.dataset.src)); if (li) { li.classList.add("hl"); li.scrollIntoView({ block: "center" }); } });
  $("#refreshBtn").onclick = () => openInsight(state.current, true);
  document.querySelectorAll("[data-fb]").forEach((el) => el.onclick = () => sendFeedback(+el.dataset.fb));
  bindChat();
}

async function loadTrace(traceId) {
  const { steps } = await api(`/api/trace/${traceId}`);
  $("#traceBox").classList.remove("loading");
  $("#traceBox").innerHTML = steps.map((s) => `<div class="trace-step">
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

async function sendFeedback(rating) {
  await api("/api/feedback", { method: "POST", body: JSON.stringify({ event_id: state.current, company_id: state.company, role: state.role, rating }) });
  $("#fbDone").textContent = "Спасибо, учтём!";
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
    card("События / компании", `${m.events} / ${m.companies}`),
  ].join("");
}

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="notice">Ошибка запуска: ${esc(e.message)}</div>`); });
