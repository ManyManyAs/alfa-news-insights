// Прототип интерфейса «Бизнес-радар» в интернет-банке. Без сборки и зависимостей.
const state = { users: [], roles: {}, user: null, company: null, role: null, feed: [], current: null, insight: null };
const $ = (s) => document.querySelector(s);
const EFFECT = { risk: "риск", opportunity: "возможность", mixed: "риски и возможности", watch: "отслеживать" };
const KIND = { fact: "Факт", assumption: "Предположение источника", bank_view: "Позиция Альфа-Банка", ai_inference: "Вывод сервиса (AI)" };
const DIR = { up: "↑", down: "↓", stable: "→", unknown: "·" };
const CONF = { high: "уверенность высокая", medium: "уверенность средняя", low: "уверенность низкая" };
const CONF_DEF = {
  high: "Высокая: подтверждено источником (новость, материал банка) или прямым признаком компании",
  medium: "Средняя: вывод сервиса по связи фактора с профилем компании; источники указаны, но связь — расчётная",
  low: "Низкая: гипотеза без прямого подтверждения — проверьте её, прежде чем действовать",
};
const PRODUCT_RU = { CL_PRODUCT_RKO_MARK: "Расчётно-кассовое обслуживание", CL_PRODUCT_LOA_MARK: "Кредит", CL_PRODUCT_LEA_MARK: "Лизинг",
  CL_PRODUCT_DEP_MARK: "Депозит", CL_PRODUCT_ZPP_MARK: "Зарплатный проект", CL_PRODUCT_GAR_MARK: "Банковские гарантии",
  FACTORING_MARK: "Факторинг", FTS_FLAG: "ВЭД и валютный контроль" };
const DISCLAIMER = "Материал носит информационно-аналитический характер, не является индивидуальной инвестиционной рекомендацией и предложением заключить сделку. Выводы с пометкой «Вывод сервиса (AI)» сформированы моделью и не являются официальной позицией Альфа-Банка.";
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

// ---- продуктовые метрики: события интерфейса копятся и отправляются пачкой (без текста, только тип и число)
const trackQueue = [];
function track(type, extra = {}) {
  if (!state.user) return;
  trackQueue.push({ type, company_id: state.company, ...extra });
  if (trackQueue.length >= 20) flushTrack();
}
function flushTrack() {
  if (!trackQueue.length || !state.user) return;
  const events = trackQueue.splice(0, 50);
  fetch("/api/track", { method: "POST", credentials: "same-origin", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ events }) }).catch(() => {});
}
setInterval(flushTrack, 5000);
// Ошибки в браузере — в мониторинг (текст и место ошибки, без данных пользователя; не больше 10 разных за сеанс)
const jsErrSeen = new Set();
function reportJsError(msg, src) {
  const key = String(msg).slice(0, 160) + "|" + (src || "");
  if (jsErrSeen.has(key) || jsErrSeen.size >= 10) return;
  jsErrSeen.add(key);
  track("js_error", { meta: { msg: String(msg).slice(0, 160), src: String(src || "").replace(location.origin, "").slice(0, 80) } });
}
addEventListener("error", (e) => { if (e.message) reportJsError(e.message, `${e.filename || ""}:${e.lineno || 0}`); });
addEventListener("unhandledrejection", (e) => { const r = e.reason || {}; if (!(r.status && r.status < 500)) reportJsError(r.message || r, "promise"); });
addEventListener("pagehide", flushTrack);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushTrack(); });

// Сессия — в HttpOnly-cookie: браузер отправляет её сам, JS её не видит
function apiError(status, text) {
  let detail = text;
  try { detail = JSON.parse(text).detail || text; } catch (e) { /* не JSON */ }
  const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  err.status = status;
  if (status === 401 && !state.loggingIn) showLogin();
  return err;
}
async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (!r.ok) throw apiError(r.status, await r.text());
  return r.json();
}

// SSE поверх fetch: прогресс агента и потоковый чат
async function sse(path, opts, onEvent) {
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers: { "Content-Type": "application/json" } });
  if (!r.ok) throw apiError(r.status, await r.text());
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

const STEP_RU = { relevance: "Оценка релевантности", plan: "План анализа", "decide:revise": "Решение: нужна ли правка моделью", draft: "Черновик интерпретации", finalize: "Итог",
  "repair:rules": "Удаление неподтверждённого", revise: "Правка по замечаниям", preview: "Быстрый вывод по профилю",
  limit: "Лимит времени или стоимости — дальше без LLM", "tool:scenario_calc": "Расчёт сценариев",
  "tool:get_story_timeline": "Хронология сюжета", "tool:get_regulation_dates": "Даты вступления в силу" };
// «Агент думает»: что сейчас делает агент — словами клиента, без названий инструментов
const THINK = { relevance: "Оцениваю, насколько событие касается компании", preview: "Готовлю быстрый вывод", plan: "Составляю план анализа",
  "tool:get_event": "Читаю новость и источники", "tool:get_event_brief": "Читаю новость и источники", "tool:get_company_profile": "Смотрю профиль компании",
  "tool:get_counterparties": "Проверяю контрагентов в цепочке платежей", "tool:get_bank_theses": "Ищу позицию Альфа-Банка",
  "tool:search_bank_analytics": "Ищу позицию Альфа-Банка", "tool:check_consistency": "Сверяю новость с прогнозами банка",
  "tool:scenario_calc": "Считаю эффект за 12 месяцев", "tool:get_peers": "Сравниваю с похожими компаниями", "tool:get_playbook": "Подбираю действия",
  "tool:get_story_timeline": "Восстанавливаю хронологию сюжета", "tool:get_regulation_dates": "Проверяю сроки вступления в силу",
  draft: "Пишу разбор", "decide:revise": "Решаю, нужна ли правка", revise: "Исправляю замечания", "repair:rules": "Убираю неподтверждённое",
  finalize: "Собираю итог", limit: "Завершаю без модели — чтобы не ждать" };
function thinkText(name) {
  if (THINK[name]) return THINK[name];
  if (name.startsWith("critique:llm")) return "Второй взгляд: другая модель проверяет разбор";
  if (name.startsWith("critique:rules")) return "Проверяю факты, числа и ссылки";
  if (name.startsWith("tool:search_bank_analytics")) return "Ищу позицию Альфа-Банка";
  return "Анализирую";
}
const THINK_STEPS = 12;
function stepTitle(name) {
  if (STEP_RU[name]) return STEP_RU[name];
  if (name.startsWith("tool:")) return "Инструмент: " + name.slice(5);
  if (name.startsWith("critique:llm")) return "Проверка LLM-критиком";
  if (name.startsWith("critique:rules")) return "Самопроверка правилами";
  return name;
}

function q(params) { return new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== "")).toString(); }

// ---------------------------------------------------------------- init / сессия
// высота шапки (она переносится на узких экранах) — для независимой прокрутки панелей ленты и разбора
function syncTopHeight() { const t = document.querySelector(".topbar"); if (t) document.documentElement.style.setProperty("--top-h", t.offsetHeight + "px"); }
try { new ResizeObserver(syncTopHeight).observe(document.querySelector(".topbar")); } catch (e) { addEventListener("resize", syncTopHeight); }

async function init() {
  const { users, roles, demo_password: demoPass } = await api("/api/demo-users");
  state.users = users; state.roles = roles; state.demoPass = demoPass;
  $("#roleSelect").innerHTML = `<option value="">Без роли</option>` + Object.entries(roles).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  bindLogin();
  $("#companySelect").onchange = () => { state.company = $("#companySelect").value; ctxLabel(); closeCtx(); reload(); };
  $("#roleSelect").onchange = () => { state.role = $("#roleSelect").value || null; ctxLabel(); closeCtx(); reload(); };
  // телефон: компания и роль — в выпадающем меню, разбор — отдельным экраном с кнопкой «Назад»
  $("#digestPdf").onclick = () => printDoc("digest");
  $("#ctxBtn").onclick = () => { const open = !document.querySelector(".topbar").classList.contains("ctx-open"); document.querySelector(".topbar").classList.toggle("ctx-open", open); $("#ctxBtn").setAttribute("aria-expanded", String(open)); };
  $("#backToFeed").onclick = () => closeDetail(true);
  addEventListener("popstate", () => { if (document.body.classList.contains("m-detail")) closeDetail(false); });
  document.querySelectorAll("[data-view]").forEach((el) => el.onclick = () => show(el.dataset.view));
  document.querySelectorAll("[data-goto]").forEach((el) => el.onclick = () => show(el.dataset.goto));
  $("#analyzeBtn").onclick = () => $("#analyzeDialog").showModal();
  bindFeedControls();
  $("#showHidden").onclick = () => { state.showHidden = !state.showHidden; loadFeed(); };
  $("#tourBtn").onclick = startTour;
  $("#heroTour").onclick = startTour;
  $("#themeBtn").onclick = toggleTheme;
  document.querySelectorAll("[data-dper]").forEach((b) => b.onclick = () => {
    state.digestPeriod = b.dataset.dper;
    document.querySelectorAll("[data-dper]").forEach((x) => x.classList.toggle("on", x === b));
    loadDigest();
  });
  document.querySelectorAll(".nav-item[data-view]").forEach((el) => { el.tabIndex = 0; el.setAttribute("role", "link");
    el.onkeydown = (e) => { if (e.key === "Enter") show(el.dataset.view); }; });
  $("#analyzeSubmit").onclick = analyzeNews;
  try { await enterApp(); } catch (e) { if (e.status !== 401) throw e; }
}

// ---------------------------------------------------------------- вход
function showLogin() {
  $("#loginView").hidden = false;
  document.body.classList.add("logged-out");
  initLoginGlobe();
  $("#demoPassHint").textContent = state.demoPass ? `· пароль для всех: ${state.demoPass}` : "";
  $("#demoList").innerHTML = state.users.map((u) => `<button type="button" class="demo-acc" data-login="${esc(u.user_id)}">
    <b>${esc(u.label)}</b><span class="muted small">${esc(state.roles[u.role] || "")} · логин ${esc(u.user_id)}</span></button>`).join("");
  document.querySelectorAll("[data-login]").forEach((b) => b.onclick = () => {
    $("#loginInput").value = b.dataset.login; $("#passInput").value = state.demoPass || ""; $("#loginForm").requestSubmit();
  });
  setTimeout(() => $("#loginInput").focus(), 50);
}
function bindLogin() {
  $("#loginForm").onsubmit = async (e) => {
    e.preventDefault();
    $("#loginErr").textContent = "";
    state.loggingIn = true;
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ login: $("#loginInput").value.trim(), password: $("#passInput").value }) });
      $("#passInput").value = "";
      await enterApp();
    } catch (err) { $("#loginErr").textContent = err.message; }
    state.loggingIn = false;
  };
  $("#logoutBtn").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); state.user = null; showLogin(); };
}

async function enterApp() {
  const me = await api("/api/me");
  $("#loginView").hidden = true;
  document.body.classList.remove("logged-out");
  initHeroGlobe();
  await selectUser(me.user_id, me);
  if ($("#view-home").classList.contains("active")) track("home_view");
  maybeOnboard();
}

async function selectUser(uid, me) {
  state.user = uid;
  const u = state.users.find((x) => x.user_id === uid);
  $("#userName").textContent = u ? u.label : uid;
  $("#userName").title = "Логин " + uid;
  const seenNames = {};
  $("#companySelect").innerHTML = me.companies.map((c) => {
    const n = (seenNames[c.name] = (seenNames[c.name] || 0) + 1);
    return `<option value="${c.uid}">${esc(c.name)}${n > 1 ? ` (${n})` : ""}</option>`;
  }).join("");
  state.company = u?.primary_company || me.companies[0]?.uid;
  $("#companySelect").value = state.company;
  state.role = u?.role || null;
  $("#roleSelect").value = state.role || "";
  ctxLabel();
  await reload();
  openFromUrl();
}
function openFromUrl() {
  const u = new URLSearchParams(location.search);
  const ev = u.get("event"), view = u.get("view");
  if (!ev && !view) return;
  history.replaceState(null, "", location.pathname);
  if (ev) { show("radar"); openInsight(ev, false, "push"); } else if (["digest", "radar", "home"].includes(view)) show(view);
}

const isPhone = () => matchMedia("(max-width: 1100px)").matches;  // одна колонка: разбор — отдельным экраном
function ctxLabel() {
  const c = $("#companySelect").selectedOptions[0], r = $("#roleSelect").selectedOptions[0];
  $("#ctxLabel").textContent = [c ? c.textContent.split(" · ")[0].slice(0, 22) : "", r && r.value ? r.textContent : ""].filter(Boolean).join(" · ") || "Компания и роль";
}
function closeCtx() { document.querySelector(".topbar").classList.remove("ctx-open"); $("#ctxBtn").setAttribute("aria-expanded", "false"); }
function openDetail() {
  if (!isPhone()) return;
  if (!document.body.classList.contains("m-detail")) history.pushState({ detail: 1 }, "");
  document.body.classList.add("m-detail");
  scrollTo({ top: 0 });
}
function closeDetail(fromButton) {
  document.body.classList.remove("m-detail");
  if (fromButton && history.state && history.state.detail) history.back();
  const card = document.querySelector(`.feed-item[data-ev="${state.current}"]`);
  if (card) card.scrollIntoView({ block: "center" });
}
function show(view) {
  if (view !== "radar") document.body.classList.remove("m-detail");
  document.body.dataset.view = view;
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  document.querySelectorAll(".nav-item[data-view]").forEach((n) => n.classList.toggle("active", n.dataset.view === view));
  if (view === "metrics") loadMetrics();
  if (view === "sources") loadSources();
  if (view === "digest") { loadDigest(); track("digest_view"); }
  if (view === "home") track("home_view");
  if (view === "radar") track("feed_view");
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
  renderTicker(d, by);
  $("#bankCard").innerHTML = `<div class="eyebrow">Позиция Альфа-Банка</div>
    <h3>Макропрогноз банка</h3>
    ${spark ? `<div class="muted small">Траектория ключевой ставки (прогноз от ${fmtDate(d.rate_path.date)})</div>${spark}` : ""}
    <table class="fc">${rows}</table>
    <div class="muted small">${d.theses_count} тезисов из ${d.documents.length} аналитических материалов · ▲▼ — пересмотр прогноза банком</div>`;
}

// ---------------------------------------------------------------- герой главной: глобус сигналов, цифры дня, бегущая строка прогнозов
function renderTicker(d, by) {
  const items = d.forecasts.filter((f) => f.period >= "2026").slice(0, 12).map((f) => {
    const r = f.revision;
    return `<span class="tk"><span class="tk-t">${esc(f.title)} ${esc(f.period)}</span><b>${fmtVal(f.value, f.unit)}</b>${r ? `<span class="tk-r ${r.direction}">${r.direction === "up" ? "▲" : "▼"} было ${fmtVal(r.from, f.unit)}</span>` : ""}</span>`;
  }).join("");
  if (!items) return;
  $("#tickerTrack").innerHTML = `<span class="tk-head">Прогноз Альфа-Банка</span>${items}<span class="tk-head">Прогноз Альфа-Банка</span>${items}`;
  void by;
}
function countUp(el, to) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches, t0 = performance.now(), from = +el.dataset.v || 0;
  el.dataset.v = to;
  if (reduce || from === to) { el.textContent = to; return; }
  const step = (now) => { const k = Math.min(1, (now - t0) / 900), e = 1 - Math.pow(1 - k, 3); el.textContent = Math.round(from + (to - from) * e); if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
function initHeroGlobe() {
  if (state.globe || typeof BizGlobe === "undefined") return;
  state.globe = new BizGlobe($("#globe"), { tip: $("#globeTip"), onPick: (s) => { show("radar"); openInsight(s.id, false, "globe"); },
    clusterTip: (items) => `<div class="gt-top"><b>${items.length} ${plural(items.length, "событие", "события", "событий")} рядом</b><span class="muted small">📍 ${esc(items[0].place || "")}</span></div>
      <ul class="gt-list">${items.slice(0, 4).map((x) => `<li><span class="dot" style="background:var(--${x.effect === "opportunity" ? "opp" : x.effect === "risk" ? "risk" : "watch"})"></span>${esc(x.title)}</li>`).join("")}</ul>
      <div class="gt-cta">Нажмите — открыть главное →</div>` });
  state.globeFilter = { days: 0, effects: new Set(["risk", "opportunity", "watch"]) };
  document.querySelectorAll("[data-gp]").forEach((b) => b.onclick = () => {
    state.globeFilter.days = +b.dataset.gp;
    document.querySelectorAll("[data-gp]").forEach((x) => x.classList.toggle("on", x === b));
    track("globe_period", { meta: { days: +b.dataset.gp } }); renderHero();
  });
  document.querySelectorAll("[data-gl]").forEach((b) => b.onclick = () => {
    const f = state.globeFilter.effects, e = b.dataset.gl;
    if (f.has(e) && f.size > 1) f.delete(e); else f.add(e);
    document.querySelectorAll("[data-gl]").forEach((x) => { const on = f.has(x.dataset.gl); x.classList.toggle("off", !on); x.setAttribute("aria-pressed", String(on)); });
    renderHero();
  });
}
function initLoginGlobe() {
  if (state.loginGlobe || typeof BizGlobe === "undefined") return;
  state.loginGlobe = new BizGlobe($("#loginGlobe"), { ambient: true });
  const hq = { geo: GEO["Москва"], label: "" };
  const pts = [["КИТАЙ", "risk"], ["ТУРЦИЯ", "opportunity"], ["ОАЭ", "watch"], ["Новосибирская область", "opportunity"], ["Приморский край", "risk"],
    ["ИНДИЯ", "watch"], ["Свердловская область", "mixed"], ["Калининградская область", "watch"], ["КАЗАХСТАН", "opportunity"], ["Республика Татарстан", "risk"]];
  state.loginGlobe.setData({ hq, signals: pts.map(([n, e], i) => ({ id: "x" + i, geo: geoOf(n), effect: e, weight: 0.3 + (i % 3) * 0.2 })) });
}
function signalOrigin(it) {
  const c = typeof cityOf === "function" ? cityOf(`${it.title} ${it.summary || ""}`) : null;
  if (c) return c;
  for (const r of it.regions || []) { const g = geoOf(r); if (g) return [g, geoShort(r)]; }
  for (const c of it.countries || []) { const g = geoOf(c); if (g) return [g, geoShort(c)]; }
  return [GEO["Москва"], "Москва, федеральный уровень"];
}
function renderHero() {
  const p = state.profile;
  if (!p || !state.feed) return;
  const feed = state.feed;
  const cnt = (e) => feed.filter((x) => x.effect === e).length;
  const opt = $("#companySelect").selectedOptions[0];
  const company = opt ? opt.textContent.split(" · ")[0] : p.okved_name;
  const region = p.region || "";
  const roleTitle = state.role ? (state.roles[state.role] || "").toLowerCase() : "";
  $("#heroDate").textContent = "Бизнес-радар · " + new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  $("#heroTitle").innerHTML = `Что в мире меняется <span class="red">для вашего бизнеса</span>`;
  $("#heroSub").innerHTML = `${esc(company)}${region ? ` · ${esc(region)}` : ""}${roleTitle ? ` · взгляд: ${esc(roleTitle)}` : ""}.<br>Из живого потока новостей отобрано <b>${feed.length}</b> ${plural(feed.length, "событие", "события", "событий")}, которые касаются именно компании — каждое с объяснением «почему вам».`;
  const stats = [["risk", "рисков", cnt("risk")], ["opportunity", "возможностей", cnt("opportunity")], ["watch", "отслеживать", cnt("watch") + cnt("mixed")]];
  const box = $("#heroStats");
  if (!box.children.length) box.innerHTML = stats.map(([k, t]) => `<button class="hs ${k}" data-hs="${k}"><b data-v="0">0</b><span>${t}</span></button>`).join("")
    + `<div class="hs bank"><b>${feed.filter((x) => x.has_bank_view).length}</b><span>с позицией банка</span></div>`;
  stats.forEach(([k, , v]) => countUp(box.querySelector(`[data-hs=${k}] b`), v));
  box.querySelector(".hs.bank b").textContent = feed.filter((x) => x.has_bank_view).length;
  box.querySelectorAll("[data-hs]").forEach((b) => b.onclick = () => {
    const eff = b.dataset.hs;
    filters.effect = eff;
    document.querySelectorAll("[data-feff]").forEach((x) => x.classList.toggle("on", x.dataset.feff === eff));
    show("radar"); renderFeedList();
  });
  if (!state.globe) return;
  const hqGeo = geoOf(region) || GEO["Москва"];
  const maxScore = Math.max(0.01, ...feed.map((x) => x.score));
  // событие в том же регионе, что и компания (чаще всего — федеральный уровень, Москва), — точка на орбите вокруг компании,
  // чтобы дуга «событие → компания» оставалась видимой; в подсказке — настоящее место
  let near = 0;
  const gf = state.globeFilter || { days: 0, effects: new Set(["risk", "opportunity", "watch"]) };
  const since = gf.days ? new Date(Date.now() - gf.days * 864e5).toISOString().slice(0, 10) : "";
  const shown = feed.filter((it) => (!since || (it.published_at || "") >= since) && gf.effects.has(it.effect === "mixed" ? "watch" : it.effect));
  const sum = $("#globeSummary");
  if (sum) sum.textContent = shown.length ? `На глобусе ${shown.length} ${plural(shown.length, "событие", "события", "событий")}: ${shown.slice(0, 3).map((x) => `${EFFECT[x.effect]} — ${x.title}`).join("; ")}.`
    : "За выбранный период событий нет.";
  const signals = shown.slice(0, 16).map((it) => {
    let [geo, place] = signalOrigin(it);
    if (Math.hypot(geo[0] - hqGeo[0], (geo[1] - hqGeo[1]) * Math.cos(hqGeo[0] * Math.PI / 180)) < 4) {
      const b = (near * 137.5 + 200) * Math.PI / 180, d = 7 + (near % 4) * 2.6;
      geo = [hqGeo[0] + d * Math.cos(b) * 0.7, hqGeo[1] + d * Math.sin(b) / Math.cos(hqGeo[0] * Math.PI / 180)];
      near += 1;
    }
    return { id: it.event_id, geo, effect: it.effect, weight: it.score / maxScore, title: it.title, place,
      tipHtml: `<div class="gt-top"><span class="pill ${it.effect}">${EFFECT[it.effect]}</span><span class="muted small">📍 ${esc(place)}</span></div>
        <b>${esc(it.title)}</b><div class="small gt-why">${esc(it.reasons[0]?.why || "")}</div><div class="gt-cta">Нажмите — открыть разбор →</div>` };
  });
  const partners = (p.fts_countries || []).slice(0, 6).map((c) => ({ geo: geoOf(c), label: geoShort(c) }));
  state.globe.setData({ hq: { geo: hqGeo, label: "Ваша компания · " + geoShort(region || "Москва") }, partners, signals });
}

// ---------------------------------------------------------------- профиль
async function loadProfile() {
  const { profile: p, exposures, peers, overrides, factors, watch } = await api(`/api/companies/${state.company}?${q({ role: state.role })}`);
  state.watch = watch || [];
  state.factors = factors; state.exposures = exposures; state.profile = p;
  renderRadar();
  renderHero();
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
    ${watchBlock()}
    ${overrides.length ? `<div class="sub-h">Что сервис понял о ваших интересах <span class="muted small">— по вашим оценкам, скрытым карточкам и настройкам</span></div>
      <ul class="learned">${overrides.map((o) => `<li class="ov ${o.multiplier === 0 ? "mute" : o.multiplier > 1 ? "more" : "less"}"><span><b>${esc(o.title)}</b> — ${o.multiplier === 0 ? "не показываем" : o.multiplier > 1 ? "показываем выше" : "показываем ниже"}<span class="muted small"> · ${esc(learnedWhy(o))}</span></span><button class="link-btn" data-unlearn="${esc(o.factor)}">Отменить</button></li>`).join("")}</ul>
      <button class="btn ghost small" id="resetPrefs">Сбросить всё</button>` : ""}
    ${supplyChain(p)}
    ${peersBlock(peers)}
    ${p.data_gaps.length ? `<div class="notice">Неполные данные: ${esc(p.data_gaps.join("; "))}</div>` : ""}
    <div class="muted small">* оценка по выборке клиентов с тем же ОКВЭД</div>`;
  bindInterests();
  bindWatch();
}

function learnedWhy(o) {
  if (o.source === "manual") return o.multiplier === 0 ? "вы скрыли тему" : "вы попросили показывать чаще";
  if (o.source === "hide") return "вы скрывали похожие карточки";
  return o.multiplier > 1 ? "вы отмечали похожие новости как полезные" : "вы отмечали похожие новости «не про мой бизнес»";
}
const WATCH_KIND = { region: "Регион", country: "Страна", industry: "Отрасль", topic: "Тема" };
function watchBlock() {
  const w = state.watch || [];
  return `<div class="sub-h">Слежу за <span class="muted small">— события по этим темам поднимаются в ленте с пометкой «вы следите»</span></div>
    <div class="chips watch-list">${w.map((x) => `<span class="chip watch">${WATCH_KIND[x.kind]}: ${esc(x.title || x.value)}<button class="x" data-unwatch="${esc(x.kind)}|${esc(x.value)}" aria-label="Убрать из списка">×</button></span>`).join("") || `<span class="muted small">Пока пусто: добавьте регион, страну, отрасль или тему, за которой хотите следить.</span>`}</div>
    <form class="watch-add" id="watchForm"><select id="watchKind" aria-label="Что добавить">${Object.entries(WATCH_KIND).map(([k, t]) => `<option value="${k}">${t}</option>`).join("")}</select>
      <input id="watchValue" list="watchOptions" placeholder="Например: Китай" aria-label="Значение" autocomplete="off"><datalist id="watchOptions"></datalist>
      <button class="btn small" type="submit">Добавить</button></form>`;
}
async function bindWatch() {
  const form = document.getElementById("watchForm");
  if (!form) return;
  if (!state.watchOpts || state.watchOpts.company !== state.company) {
    try { state.watchOpts = { company: state.company, ...(await api(`/api/watch/options?${q({ company_id: state.company })}`)) }; } catch (e) { state.watchOpts = null; }
  }
  const fill = () => {
    const kind = $("#watchKind").value, opts = (state.watchOpts || {})[kind] || [];
    $("#watchOptions").innerHTML = opts.map((o) => `<option value="${esc(typeof o === "string" ? o : o.title)}"></option>`).join("");
    $("#watchValue").placeholder = { region: "Например: Новосибирская область", country: "Например: Китай", industry: "Например: Строительство зданий", topic: "Например: Курс рубля" }[kind];
  };
  $("#watchKind").onchange = fill; fill();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const kind = $("#watchKind").value, text = $("#watchValue").value.trim();
    if (!text) return;
    const opts = (state.watchOpts || {})[kind] || [];
    const hit = opts.find((o) => (typeof o === "string" ? o : o.title).toLowerCase() === text.toLowerCase());
    if ((kind === "topic" || kind === "industry") && !hit) { toast("Выберите вариант из списка"); return; }
    const value = hit ? (typeof hit === "string" ? hit : hit.value) : text;
    try {
      await api(`/api/companies/${state.company}/watch`, { method: "POST", body: JSON.stringify({ kind, value }) });
      toast(`Следим за: ${hit && hit.title ? hit.title : value}. Такие события будут выше в ленте`);
      track("watch_add", { meta: { kind } });
      await Promise.all([loadProfile(), loadFeed()]);
    } catch (err) { toast(err.message); }
  };
  document.querySelectorAll("[data-unwatch]").forEach((b) => b.onclick = async () => {
    const [kind, value] = b.dataset.unwatch.split("|");
    await api(`/api/companies/${state.company}/watch?${q({ kind, value })}`, { method: "DELETE" });
    await Promise.all([loadProfile(), loadFeed()]);
  });
  document.querySelectorAll("[data-unlearn]").forEach((b) => b.onclick = async () => {
    await api(`/api/companies/${state.company}/preferences`, { method: "PUT", body: JSON.stringify({ factor: b.dataset.unlearn, level: "normal" }) });
    toast("Отменено — тема снова как обычно");
    await Promise.all([loadProfile(), loadFeed()]);
  });
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
  const dots = weight >= 1 ? "●●●" : weight >= 0.5 ? "●●○" : "●○○";
  return `<button class="chip interest ${lvl}" data-factor="${esc(factor)}" data-level="${lvl}" title="${esc(tip)}">${LEVEL_ICON[lvl]}${esc(title)} <span class="w-dots">${dots}</span></button>`;
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
  return `<div class="sub-h">Цепочка поставок <span class="muted small">— по платежам компании</span></div>
    ${buyers.length || p.unknown_share_in ? `<div class="muted small">Покупатели · ${p.n_buyers} · концентрация ${conc(p.hhi_in)}</div>${mixBars(buyers, p.unknown_share_in || 0)}` : ""}
    ${suppliers.length || p.unknown_share_out ? `<div class="muted small">Поставщики · ${p.n_suppliers} · концентрация ${conc(p.hhi_out)}</div>${mixBars(suppliers, p.unknown_share_out || 0)}` : ""}
    <div class="chips">${p.network_pct != null ? `<span class="chip" title="По объёму и связям платежей компания заметнее, чем ${Math.round(p.network_pct * 100)}% клиентов в выборке">${p.network_pct >= 0.66 ? "Заметный участник цепочек поставок" : p.network_pct >= 0.33 ? "Обычный участник цепочек поставок" : "Небольшой участник цепочек поставок"}</span>` : ""}
      ${p.affiliates.length ? `<span class="chip" title="Компании, связанные через общих владельцев или руководителей">Связанных компаний в группе: ${p.affiliates.length}</span>` : ""}</div>`;
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
  const { items, quiet, hidden_count: hiddenCount } = await api(`/api/feed?${q({ company_id: state.company, role: state.role, limit: 40, show_hidden: state.showHidden ? "true" : null })}`);
  $("#showHidden").hidden = !hiddenCount;
  $("#showHidden").textContent = state.showHidden ? "Убрать скрытые" : `Показать скрытые (${hiddenCount})`;
  state.feed = items; state.feedKey = `${state.company}|${state.role}`; state.rerank = null; state.quiet = quiet;
  $("#navBadge").textContent = items.filter((i) => i.effect === "risk").length || "";
  renderWidget();
  fillFactorFilter();
  renderFeedList();
  renderRadar();
  renderHero();
  applyRerank(state.feedKey);
}

function renderWidget() {
  // на главную — только то, что модель не считает слабо связанным с компанией
  const items = state.feed.filter((it) => !(it.ai_relevance && it.ai_relevance.level <= 1)), quiet = state.quiet, read = readSet();
  $("#widgetList").innerHTML = items.slice(0, 4).map((it) => `
    <div class="w-item eff-${it.effect} ${read.has(it.event_id) ? "read" : ""}" data-ev="${it.event_id}" tabindex="0" role="button">
      ${read.has(it.event_id) ? "" : `<span class="new-dot" title="Не прочитано"></span>`}<span class="pill ${it.effect}">${EFFECT[it.effect]}</span><span class="muted small">${fmtDate(it.published_at)}</span>${read.has(it.event_id) ? `<span class="read-mark">✓ прочитано</span>` : ""}
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
const filters = { effect: "all", factor: "", period: "all", text: "", sort: "recommended", unread: false };
function readSet() { try { return new Set(JSON.parse(localStorage.getItem("read:" + state.company) || "[]")); } catch (e) { return new Set(); } }
function markRead(id) {
  const s = readSet(); s.add(id);
  try { localStorage.setItem("read:" + state.company, JSON.stringify([...s].slice(-500))); } catch (e) { /* хранилище недоступно */ }
  if (document.getElementById("feedList")) { renderFeedList(); renderWidget(); }
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
  // «По важности» — строго по шкале; «Рекомендуемые» — порядок сервиса: важность плюс разнообразие тем наверху
  else if (filters.sort === "importance") list = [...list].sort((a, b) => imp(b) - imp(a));
  return list;
}
const imp = (it) => it.importance_score ?? it.score;
// Шкала важности: доля от самой важной карточки ленты, словами — в подсказке и для экранного диктора
function impBar(it) {
  const max = Math.max(0.01, ...state.feed.map(imp)), v = imp(it) / max;
  const word = v >= 0.75 ? "высокая" : v >= 0.45 ? "средняя" : "низкая";
  const why = it.reasons.slice(0, 3).map((r) => r.factor_title).join(", ");
  return `<div class="imp-row" title="Важность для компании: ${word}. Учитывает, чем событие связано с компанией (${esc(why)}), свежесть, значимость и оценку ИИ после разбора.">
    <span class="imp-lbl">важность: ${word}</span><div class="score-bar" role="meter" aria-label="Важность для компании: ${word}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(v * 100)}"><i style="width:${Math.max(4, Math.round(v * 100))}%"></i></div></div>`;
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
    return `<div class="feed-item eff-${it.effect} ${read.has(it.event_id) ? "read" : ""} ${it.event_id === state.current ? "active" : ""}" data-ev="${it.event_id}" tabindex="0" role="button">
      ${it.hidden ? `<button class="link-btn unhide" data-unhide="${it.event_id}">Вернуть в ленту</button>` : `<button class="hide-btn" data-hide="${it.event_id}" title="Скрыть из ленты" aria-label="Скрыть из ленты">×</button>`}
      <div><span class="pill ${it.effect}">${EFFECT[it.effect]}</span>${it.has_bank_view ? `<span class="pill bank">есть позиция банка</span>` : ""}${effectiveTag(it.effective)}</div>
      <div class="title">${read.has(it.event_id) ? "" : `<span class="new-dot" title="Не прочитано"></span>`}${esc(it.title)}${read.has(it.event_id) ? `<span class="read-mark">✓ прочитано</span>` : ""}</div>
      <div class="meta">${fmtDate(it.published_at)} · ${esc([...new Set(it.sources.map((x) => x.source))].join(", "))}${it.sources.length > 1 ? ` · ${it.sources.length} ${plural(it.sources.length, "публикация", "публикации", "публикаций")}` : ""}</div>
      <div class="chips">${entityChips(it)}</div>
      <div class="reasons">${esc(reasonText(it))}</div>
      ${personalNote(it)}
      ${n ? `<div class="ai-note"><b>ИИ:</b> ${esc(n.why)} <span class="rel r${n.relevance}">${"●".repeat(n.relevance)}${"○".repeat(3 - n.relevance)}</span></div>` : ""}
      ${it.ai_relevance && it.ai_relevance.level <= 1 ? `<div class="ai-weak" title="${esc(it.ai_relevance.why)}">ИИ после разбора: ${it.ai_relevance.level === 0 ? "не касается компании" : "слабо связано с бизнесом"} — карточка ниже в ленте</div>` : ""}
      ${it.ai_relevance && it.ai_relevance.level === 3 ? `<div class="ai-strong" title="${esc(it.ai_relevance.why)}">ИИ после разбора: существенно для компании</div>` : ""}
      ${it.story ? `<div class="story-badge">Сюжет · ${it.story.count} ${plural(it.story.count, "событие", "события", "событий")}: ${esc(fmtDate(it.story.timeline[0].published_at))} → ${esc(fmtDate(it.story.timeline[it.story.timeline.length - 1].published_at))}</div>` : ""}
      ${impBar(it)}
    </div>`;
  }).join("") || `<div class="empty">${state.feed.length ? "Под фильтры ничего не подошло — сбросьте их." : "Значимых событий нет."}</div>`;
  if (before) for (const el of list.children) {
    const dy = (before.get(el.dataset.ev) ?? el.getBoundingClientRect().top) - el.getBoundingClientRect().top;
    if (dy) el.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 450, easing: "ease-out" });
  }
  bindCards(list);
}
function personalNote(it) {
  const r = it.reasons.find((x) => x.personal);
  if (!r) return "";
  const m = r.personal.multiplier, why = r.personal.why;
  const lead = r.factor === "watch" ? "👁" : m === 0 ? "🔇" : m > 1 ? "▲ Выше в ленте:" : "▼ Ниже в ленте:";
  return `<div class="personal${m < 1 ? " down" : ""}">${lead} ${esc(why.replace(/^Вы /, "вы "))}</div>`;
}
async function hideEvent(id, hidden = true) {
  await api("/api/feed/hide", { method: "POST", body: JSON.stringify({ company_id: state.company, event_id: id, hidden }) });
  if (hidden) {
    state.feed = state.feed.filter((x) => x.event_id !== id);
    renderFeedList(); renderWidget();
    toast("Карточка скрыта, похожие будут ниже. Вернуть — кнопкой «Показать скрытые» над лентой");
    loadProfile();
  } else { await loadFeed(); }
}
function bindCards(root) {
  root.querySelectorAll("[data-hide]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); hideEvent(b.dataset.hide); });
  root.querySelectorAll("[data-unhide]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); hideEvent(b.dataset.unhide, false); });
  root.querySelectorAll("[data-ev]").forEach((el) => {
    const from = root.id === "widgetList" ? "widget" : root.id === "digestBox" ? "digest" : "feed";
    const open = () => {
      if (from === "widget") track("widget_click", { event_id: el.dataset.ev });
      if (from !== "feed") show("radar");
      openInsight(el.dataset.ev, false, from);
    };
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
  $("#rerankNote").textContent = "✨ Порядок уточнён ИИ под ваш профиль";
}

// Радар на главной: чувствительность компании и сигналы ленты
function renderRadar() {
  const box = document.getElementById("radarChart");
  if (!box || !state.exposures) return;
  box.innerHTML = factorRadar(state.exposures, state.feed) || `<div class="muted small">Недостаточно данных о компании.</div>`;
}

// ---------------------------------------------------------------- инсайт
async function openInsight(eventId, refresh = false, from = "feed") {
  state.current = eventId;
  const t0 = performance.now();
  openDetail();
  if (!refresh) track("card_open", { event_id: eventId, meta: { from } });
  markRead(eventId);
  document.querySelectorAll(".feed-item").forEach((el) => el.classList.toggle("active", el.dataset.ev === eventId));
  $("#insightPanel").innerHTML = `<div class="insight"><div id="previewBox"></div>
    <div class="think" role="status" aria-live="polite">
      <div class="think-orb" aria-hidden="true"><i></i><i></i><i></i></div>
      <div class="think-body"><div class="eyebrow">Агент готовит разбор для вашей компании</div>
        <div class="think-now" id="thinkNow">Оцениваю, насколько событие касается компании</div>
        <div class="think-bar" aria-hidden="true"><i id="thinkBar"></i></div>
        <div class="think-meta muted small" id="thinkMeta">прошло 0 с · обычно 15–45 с</div>
        <ol class="think-steps" id="agentProgress" aria-label="Выполненные шаги"></ol></div></div></div>`;
  const prog = () => document.getElementById("agentProgress");
  let done = 0;
  clearInterval(state.thinkTimer);
  state.thinkTimer = setInterval(() => {
    const m = document.getElementById("thinkMeta");
    if (!m || state.current !== eventId) return clearInterval(state.thinkTimer);
    const sec = Math.round((performance.now() - t0) / 1000);
    m.textContent = `прошло ${sec} с · ${sec < 45 ? "обычно 15–45 с" : "почти готово — модель дописывает разбор"} · шагов: ${done}`;
  }, 1000);
  try {
    await sse(`/api/insights/${eventId}/stream?${q({ company_id: state.company, role: state.role, refresh })}`, {}, (e) => {
      if (state.current !== eventId || !prog()) return;
      if (e.type === "step_start") {
        const now = document.getElementById("thinkNow");
        if (now && now.textContent !== thinkText(e.name)) { now.classList.remove("swap"); void now.offsetWidth; now.textContent = thinkText(e.name); now.classList.add("swap"); }
      } else if (e.type === "step_end") {
        done += 1;
        const bar = document.getElementById("thinkBar");
        if (bar) bar.style.width = Math.min(94, 6 + done / THINK_STEPS * 88) + "%";
        const txt = thinkText(e.name);
        if (![...prog().children].some((x) => x.dataset.t === txt)) prog().insertAdjacentHTML("beforeend", `<li class="${e.fallback ? "fallback" : "ok"}" data-t="${esc(txt)}">${esc(txt)}${e.fallback ? " · упрощённо" : ""}</li>`);
      } else if (e.type === "preview") {
        track("preview_ready", { event_id: eventId, value: Math.round(performance.now() - t0) });
        // главный вывод сразу, полный разбор догружается
        const p = e.insight;
        $("#previewBox").innerHTML = `<span class="pill ${p.effect}">${EFFECT[p.effect]}</span> <span class="muted small">быстрый вывод по профилю · полный разбор готовится…</span>
          <h2 style="margin-top:8px">${esc(p.headline)}</h2>
          <div class="takeaway"><span class="claim-kind small" style="color:var(--ai)">ВЫВОД СЕРВИСА · </span>${esc(p.takeaway.text)}</div>
          ${scenarioCards(p.scenarios, true)}`;
      } else if (e.type === "done") {
        clearInterval(state.thinkTimer);
        track("insight_ready", { event_id: eventId, value: Math.round(performance.now() - t0), meta: { cached: !!e.cached } });
        state.insight = e.insight; renderInsight("analysis");
      } else if (e.type === "error") { throw new Error(e.detail); }
    });
  } catch (e) { $("#insightPanel").innerHTML = `<div class="insight bad">Ошибка: ${esc(e.message)}</div>`; }
}

// Метка источника у утверждения: понятное слово вместо внутреннего id, полное название — в подсказке
function srcTags(ids) {
  const seen = new Set();
  return (ids || []).filter((s) => { const k = CITE_KIND[s.split(":")[0]] || s.split(":")[0]; if (seen.has(k)) return false; seen.add(k); return true; }).map((s) => {
    const kind = s.split(":")[0];
    const src = (state.insight?.sources || []).find((x) => x.id === s);
    const label = CITE_KIND[kind] || (kind === "peers" ? "похожие компании" : "источник");
    return `<span class="src ${esc(kind)}" data-src="${esc(s)}" title="${esc(src ? src.title : s)}">${label}</span>`;
  }).join("");
}
function claims(list) {
  return (list || []).map((c) => `<div class="claim ${c.kind} ${c.confidence === "low" ? "conf-low" : ""}">
    <span class="kind">${KIND[c.kind] || c.kind}</span>${esc(c.text)}${c.owner || c.due ? `<div class="act-meta">${c.owner ? `<span class="am-owner">👤 ${esc(c.owner)}</span>` : ""}${c.due ? `<span class="am-due">⏱ ${esc(c.due)}</span>` : ""}</div>` : ""}<div>${srcTags(c.sources)}${c.kind !== "fact" ? `<span class="conf ${c.confidence}" title="${esc(CONF_DEF[c.confidence] || "")}">${CONF[c.confidence] || ""}</span>` : ""}</div></div>`).join("") || `<div class="muted small">нет данных</div>`;
}

const SCEN_CLASS = { base: "watch", negative: "risk", positive: "opportunity" };
const SCEN_SHORT = { negative: "Негативный", base: "Базовый", positive: "Позитивный" };
const SCEN_ORDER = ["negative", "base", "positive"];
function scenarioCards(list, compact = false) {
  if (!list || !list.length) return "";
  const est = list[0].estimated || [];
  const byName = Object.fromEntries(list.map((s) => [s.name, s]));
  const cards = `<div class="scenarios">${SCEN_ORDER.filter((n) => byName[n]).map((n) => { const s = byName[n]; return `<div class="scen ${SCEN_CLASS[s.name]}">
      <div class="scen-t">${esc(s.title)}</div>
      <div class="scen-v ${s.total_rub < 0 ? "bad" : s.total_rub > 0 ? "ok" : ""}">${rub(s.total_rub)}</div>
      ${s.share_of_revenue != null ? `<div class="muted small">${pctRev(s.share_of_revenue)} выручки</div>` : ""}
      <div class="small">${esc(s.assumptions)}</div></div>`; }).join("")}</div>`;
  if (compact) return cards;
  return cards + effectChart(list) + leverTable(list)
    + `<div class="muted small scen-note">${est.length ? `Оценено по профилю: ${esc(est.join("; "))}. ` : ""}Суммы считает калькулятор по формулам, а не модель. Свои цифры — в блоке «Что если».</div>`;
}
function pctRev(x) {
  const v = Math.abs(x * 100);
  return (x < 0 ? "−" : "") + (v < 0.1 ? v.toFixed(2) : v.toFixed(1)).replace(".", ",") + "%";
}
// Накопленный эффект по месяцам: три линии сценариев, ноль, подписи итогов и месяц начала действия нормы
function effectChart(list) {
  const ok = list.filter((s) => (s.monthly || []).length === 12);
  if (!ok.length || ok.every((s) => s.monthly.every((v) => !v))) return "";
  const months = ok[0].months || [], start = ok[0].start_month || 0;
  const W = 640, H = 210, L = 64, R = 92, T = 14, B = 30;
  const vals = ok.flatMap((s) => s.monthly).concat([0]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (k) => L + (k + 1) * (W - L - R) / 12, y = (v) => T + (hi - v) * (H - T - B) / (hi - lo);
  const col = { negative: "var(--risk)", base: "var(--watch)", positive: "var(--opp)" };
  const ticks = [lo + pad, 0, hi - pad].filter((v, i, a) => a.findIndex((z) => Math.abs(z - v) < (hi - lo) * 0.12) === i);
  const lines = SCEN_ORDER.map((n) => ok.find((s) => s.name === n)).filter(Boolean).map((s) => {
    const pts = [[L, y(0)], ...s.monthly.map((v, k) => [x(k), y(v)])];
    const last = s.monthly[11];
    return `<path d="M${pts.map((p) => p.map((z) => z.toFixed(1)).join(",")).join(" L")}" fill="none" stroke="${col[s.name]}" stroke-width="${s.name === "base" ? 3 : 2}" ${s.name === "base" ? "" : 'stroke-dasharray="5 4"'} stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${x(11).toFixed(1)}" cy="${y(last).toFixed(1)}" r="4" fill="${col[s.name]}"/>
      <text x="${(x(11) + 8).toFixed(1)}" y="${(y(last) + 4).toFixed(1)}" class="ec-end" fill="${col[s.name]}">${esc(rub(last))}</text>`;
  });
  // подписи у правого края не должны налезать друг на друга
  const startMark = start > 0 ? `<line x1="${x(start - 1).toFixed(1)}" x2="${x(start - 1).toFixed(1)}" y1="${T}" y2="${H - B}" class="ec-start"/>
    <text x="${(x(start - 1) + 4).toFixed(1)}" y="${T + 10}" class="ec-lbl">вступление в силу</text>` : "";
  return `<div class="effect-chart"><div class="ec-head">Как накапливается эффект по месяцам</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Накопленный эффект по трём сценариям за 12 месяцев">
      ${ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="${Math.abs(v) < 1e-6 ? "ec-zero" : "ec-grid"}"/><text x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" class="ec-ax" text-anchor="end">${v === 0 ? "0" : esc(rub(v))}</text>`).join("")}
      ${months.map((m, k) => k % 2 === 0 || k === 11 ? `<text x="${x(k).toFixed(1)}" y="${H - 10}" class="ec-ax" text-anchor="middle">${esc(m)}</text>` : "").join("")}
      ${startMark}${lines.join("")}
    </svg>
    <div class="ec-legend">${SCEN_ORDER.filter((n) => ok.find((s) => s.name === n)).map((n) => `<span><i class="${n}"></i>${SCEN_SHORT[n]}</span>`).join("")}</div></div>`;
}
// Из чего складывается итог: рычаг × сценарий, под рычагом — через что он действует на компанию
function leverTable(list) {
  const byName = Object.fromEntries(list.map((s) => [s.name, s]));
  const levers = [];
  list.forEach((s) => (s.rows || []).forEach((r) => { if (!levers.find((l) => l.lever === r.lever)) levers.push(r); }));
  if (!levers.length) return "";
  const cell = (n, lv) => { const r = ((byName[n] || {}).rows || []).find((x) => x.lever === lv); return r ? `<td class="${r.effect_rub < 0 ? "bad" : r.effect_rub > 0 ? "ok" : "muted"}" title="${esc(r.formula)}">${rub(r.effect_rub)}</td>` : `<td class="muted">—</td>`; };
  return `<div class="lever-wrap"><table class="lever-table"><thead><tr><th>Из чего складывается</th>${SCEN_ORDER.filter((n) => byName[n]).map((n) => `<th>${SCEN_SHORT[n]}</th>`).join("")}</tr></thead><tbody>
    ${levers.map((l) => { const b = ((byName.base || {}).rows || []).find((x) => x.lever === l.lever) || l; return `<tr><td><b>${esc(l.title)}</b>${b.basis ? `<div class="muted small">${esc(b.basis)}</div>` : ""}<div class="muted small lt-f">${esc(b.formula)}</div></td>${SCEN_ORDER.filter((n) => byName[n]).map((n) => cell(n, l.lever)).join("")}</tr>`; }).join("")}
    <tr class="lt-total"><td>Итого за 12 месяцев</td>${SCEN_ORDER.filter((n) => byName[n]).map((n) => `<td class="${byName[n].total_rub < 0 ? "bad" : byName[n].total_rub > 0 ? "ok" : ""}">${rub(byName[n].total_rub)}</td>`).join("")}</tr>
  </tbody></table></div>`;
}
function rub(v) {
  if (v == null) return "—";
  if (Math.abs(v) < 500) return "0 ₽";
  const a = Math.abs(v), sign = v < 0 ? "−" : v > 0 ? "+" : "";
  const n = (x) => x.toFixed(1).replace(".", ",");
  return sign + (a >= 1e9 ? n(a / 1e9) + " млрд ₽" : a >= 1e6 ? n(a / 1e6) + " млн ₽" : Math.round(a / 1e3) + " тыс. ₽");
}

// «Что если»: слайдеры шоков и свои суммы → пересчёт на сервере тем же калькулятором, что у агента
function whatIfBlock() {
  return `<details class="whatif" id="whatIf"><summary>Что если — посчитать для своих цифр</summary><div id="whatIfBody" class="muted small">Загрузка…</div></details>`;
}
const WI = { debt_rub: ["Долг, млн ₽", 1e6], floating_share: ["Доля под плавающей ставкой, %", 0.01], import_rub: ["Импорт в год, млн ₽", 1e6], payroll_rub: ["ФОТ в год, млн ₽", 1e6], margin: ["Маржинальность, %", 0.01], purchases_rub: ["Закупки в год, млн ₽", 1e6] };
const fmtPct = (x, unit = "%") => `${x > 0 ? "+" : x < 0 ? "−" : ""}${String(Math.abs(x)).replace(".", ",")}${unit}`;
async function initWhatIf() {
  const box = document.getElementById("whatIfBody");
  if (!box) return;
  const r = await api("/api/scenario", { method: "POST", body: JSON.stringify({ company_id: state.company, event_id: state.current, role: state.role }) });
  const b = r.baseline, d = r.defaults, biz = r.business || {}, bp = r.base_params || {};
  state.wi = { biz };
  const slider = (id, label, min, max, step, val, hint) => `<label class="wi-row"><span>${label}</span>
    <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"><b id="${id}V"></b><span class="muted small">${hint}</span></label>`;
  const fields = Object.keys(WI).filter((k) => !((k === "margin" && !biz.demand) || (k === "purchases_rub" && !biz.costs)));
  const field = (k) => `<label class="wi-field">${WI[k][0]}<input type="number" min="0" step="any" id="wi_${k}" value="${d[k] != null ? +(d[k] / WI[k][1]).toFixed(2) : ""}" placeholder="нет данных"></label>`;
  box.className = "";
  box.innerHTML = `
    <div class="wi-grid">
      ${biz.demand ? slider("wiDem", `Спрос на продукцию, %`, -20, 20, 0.5, bp.demand_change_pct ?? 0, `в базовом сценарии: ${fmtPct(bp.demand_change_pct ?? 0)} — ${esc(biz.demand.shock_src)}; ${esc(biz.demand.basis)}`) : ""}
      ${biz.costs ? slider("wiCost", `Цены закупок, %`, -15, 30, 0.5, bp.cost_change_pct ?? 0, `в базовом сценарии: ${fmtPct(bp.cost_change_pct ?? 0)} — ${esc(biz.costs.shock_src)}; ${esc(biz.costs.basis)}`) : ""}
      ${biz.tax ? slider("wiTax", "Налоговая нагрузка, п.п. выручки", -2, 2, 0.05, bp.tax_change_pp ?? 0, `в базовом сценарии: ${fmtPct(bp.tax_change_pp ?? 0, " п.п.")} — стресс-сценарий, уточните по своей отчётности`) : ""}
      ${slider("wiRate", "Ключевая ставка, изменение", -4, 4, 0.25, b.rate_delta_pp, `прогноз банка: ${fmtPct(b.rate_delta_pp, " п.п.")} (средняя ${String(b.key_rate_avg_next).replace(".", ",")}% против ${String(b.key_rate_now).replace(".", ",")}% сейчас)`)}
      ${slider("wiFx", "Курс рубля: ослабление, %", -20, 40, 0.1, b.fx_change_pct, `прогноз банка: ${String(b.fx_change_pct).replace(".", ",")}% (${b.usd_rub.join(" → ")} ₽/$)`)}
      ${slider("wiWage", "Рост зарплат за год, %", 0, 25, 0.1, b.wage_growth_pct, `прогноз банка: ${String(b.wage_growth_pct).replace(".", ",")}% — заложен в бюджет как база`)}
    </div>
    <div class="wi-fields">${fields.map(field).join("")}</div>
    <div id="wiOut" class="wi-out"></div>
    <div class="muted small">${r.estimated.length ? "Значения по умолчанию — оценки по профилю: " + esc(r.estimated.join("; ")) + ". Замените своими — расчёт сразу обновится." : ""}${r.start_month ? ` Отраслевой эффект считается с ${esc(r.months[r.start_month])} — с даты вступления в силу.` : ""}</div>`;
  let timer;
  const run = () => { clearTimeout(timer); timer = setTimeout(recalc, 120); };
  box.querySelectorAll("input").forEach((el) => el.addEventListener("input", run));
  recalc();
}
async function recalc() {
  const el = (id) => document.getElementById(id);
  const v = (id) => +el(id).value;
  el("wiRateV").textContent = fmtPct(v("wiRate"), " п.п.");
  el("wiFxV").textContent = fmtPct(v("wiFx"));
  el("wiWageV").textContent = `${String(v("wiWage")).replace(".", ",")}%`;
  const inputs = { rate_delta_pp: v("wiRate"), fx_change_pct: v("wiFx"), wage_growth_pct: v("wiWage") };
  if (el("wiDem")) { inputs.demand_change_pct = v("wiDem"); el("wiDemV").textContent = fmtPct(v("wiDem")); }
  if (el("wiCost")) { inputs.cost_change_pct = v("wiCost"); el("wiCostV").textContent = fmtPct(v("wiCost")); }
  if (el("wiTax")) { inputs.tax_change_pp = v("wiTax"); el("wiTaxV").textContent = fmtPct(v("wiTax"), " п.п."); }
  for (const k of Object.keys(WI)) { const f = el("wi_" + k); if (f && f.value !== "") inputs[k] = +f.value * WI[k][1]; }
  try {
    const { result: r } = await api("/api/scenario", { method: "POST", body: JSON.stringify({ company_id: state.company, event_id: state.current, role: state.role, inputs }) });
    $("#wiOut").innerHTML = `<div class="wi-total ${r.total_rub < 0 ? "bad" : r.total_rub > 0 ? "ok" : ""}">${rub(r.total_rub)} <span class="muted small">за 12 месяцев к плану${r.share_of_revenue != null ? ` · ${pctRev(r.share_of_revenue)} выручки` : ""}</span></div>
      ${r.rows.map((x) => `<div class="wi-line"><span>${esc(x.title)}</span><b class="${x.effect_rub < 0 ? "bad" : x.effect_rub > 0 ? "ok" : ""}">${rub(x.effect_rub)}</b><span class="muted small">${esc(x.formula)}</span></div>`).join("")}`;
  } catch (e) { $("#wiOut").textContent = "Не удалось посчитать: " + e.message; }
}

// «Коротко»: что случилось → что это значит (с оценкой в ₽ из калькулятора) → что сделать (кто и к какому сроку)
function shortBlock(i) {
  const fact = (i.what_happened || []).find((c) => c.kind === "fact") || (i.what_happened || [])[0];
  const sc = Object.fromEntries((i.scenarios || []).map((s) => [s.name, s]));
  const base = sc.base, lo = Math.min(...(i.scenarios || []).map((s) => s.total_rub)), hi = Math.max(...(i.scenarios || []).map((s) => s.total_rub));
  const act = (i.actions || []).find((a) => a.due) || (i.actions || [])[0];
  const row = (t, html) => `<div class="sh-row"><div class="sh-k">${t}</div><div class="sh-v">${html}</div></div>`;
  return `<div class="takeaway short">
    <div class="sh-head"><span class="claim-kind">КОРОТКО</span>${i.materiality != null ? `<span class="sh-mat m${i.materiality}" title="${esc(i.materiality_why || "")}">${["не касается компании", "слабая связь", "заметное влияние", "существенно для компании"][i.materiality]}</span>` : ""}</div>
    ${fact ? row("Что случилось", esc(fact.text)) : ""}
    ${row("Что это значит", `${esc(i.takeaway.text)}${base && (lo || hi) ? `<div class="sh-range">Эффект за 12 месяцев к текущим условиям: базовый сценарий <b class="${base.total_rub < 0 ? "neg" : base.total_rub > 0 ? "pos" : ""}">${rub(base.total_rub)}</b>
      <span class="sh-note">диапазон от ${rub(lo)} до ${rub(hi)}${(base.rows || []).length ? " · больше всего — " + esc(base.rows.slice().sort((a, b) => Math.abs(b.effect_rub) - Math.abs(a.effect_rub))[0].title.replace(/^./, (c) => c.toLowerCase())) : ""}${base.start_month ? " · начнётся с " + esc((base.months || [])[base.start_month] || "") : ""} · расчёт и разбивка — в подробном разборе</span></div>` : ""}`)}
    ${act ? row("Что сделать", `${esc(act.text)}${act.owner || act.due ? `<div class="sh-meta">${act.owner ? `👤 ${esc(act.owner)}` : ""}${act.due ? ` · ⏱ ${esc(act.due)}` : ""}</div>` : ""}`) : ""}
    <div class="sh-src">${srcTags(i.takeaway.sources)}</div></div>`;
}
// Новость и позиция банка расходятся — показываем наверху оба взгляда, без выбора «кто прав»
function divergenceBlock(i) {
  const issues = (i.relevance && i.relevance.consistency_issues) || [];
  if (!issues.length) return "";
  return `<div class="diverge"><div class="dv-head">Рынок и Альфа-Банк смотрят по-разному</div>
    ${issues.slice(0, 2).map((x) => `<div class="dv-row">${esc(x)}</div>`).join("")}
    <div class="muted small">Сервис показывает оба взгляда. Вывод в таких местах — мнение модели, а не позиция банка.</div></div>`;
}

function renderInsight(tab) {
  const i = state.insight;
  const allOk = i.quality.every((c) => c.passed);
  const tabs = [["analysis", "Интерпретация"], ["sources", `Источники (${i.sources.length})`], ["trace", "Как получен вывод"]];
  let body = "";
  if (tab === "analysis") {
    body = `
      <details class="impact-wrap" open><summary>Карта влияния: как событие доходит до вашей компании</summary>${impactMap(i)}</details>
      <div class="kinds"><span class="k-fact" title="Подтверждено новостью или документом">Факт</span><span class="k-assumption" title="Прогноз или мнение из источника — не факт">Предположение источника</span><span class="k-bank_view" title="Тезис или прогноз из аналитики Альфа-Банка со ссылкой на документ и страницу">Позиция Альфа-Банка</span><span class="k-ai_inference" title="Вывод модели, не официальная позиция банка">Вывод сервиса (AI) — не позиция банка</span></div>
      <div class="q"><h4><span class="n">1</span>Что произошло</h4>${claims(i.what_happened)}</div>
      <div class="q"><h4><span class="n">2</span>Почему это важно для вашей компании</h4>${claims(i.why_it_matters)}</div>
      <div class="q"><h4><span class="n">3</span>Возможные последствия</h4>
        <div class="cols"><div><b class="bad small">Риски</b>${claims(i.consequences.risks)}</div><div><b class="ok small">Возможности</b>${claims(i.consequences.opportunities)}</div></div>
        <b class="small muted">Что повлияет на развитие ситуации</b>${claims(i.consequences.drivers)}
        ${(i.scenarios || []).length ? `<div class="sub-h">Эффект для компании за 12 месяцев</div>${scenarioCards(i.scenarios)}` : ""}
        ${(i.what_could_change || []).length ? `<div class="sub-h">Что может изменить вывод</div>${claims(i.what_could_change)}` : ""}
        ${whatIfBlock()}</div>
      <div class="q"><h4><span class="n">4</span>Что можно сделать</h4>${claims(i.actions)}
        ${i.products.map((p) => `<div class="product"><span class="lbl">${esc(p.label)}</span><br><b>${esc(PRODUCT_RU[p.product] || p.product)}</b> — ${esc(p.why)}
          <div><button class="btn small" data-act="application" data-product="${esc(PRODUCT_RU[p.product] || p.product)}">Оставить заявку</button></div></div>`).join("")}
        <div class="cta"><button class="btn small" data-act="manager">Обсудить с менеджером</button><button class="btn ghost small" data-act="callback">Заказать обратный звонок</button></div></div>
      <div class="q"><h4><span class="n">5</span>Как это оценивает Альфа-Банк</h4>${claims(i.bank_view)}</div>
      <div class="q"><h4><span class="n">6</span>На чём основан вывод</h4>
        <div class="small muted">Каждое утверждение выше помечено типом и источником. Автоматическая проверка разбора (${i.iterations} ${plural(i.iterations, "проход", "прохода", "проходов")}):</div>
        <ul class="quality">${i.quality.map((c) => `<li class="${c.passed ? "ok" : "bad"}">${c.passed ? "✓" : "✗"} ${esc(CHECK_RU[c.name] || c.name)}${c.passed ? "" : " — " + esc(cleanRefs(c.detail))}</li>`).join("")}</ul>
        <div class="muted small scale">Шкала уверенности: ${Object.values(CONF_DEF).map(esc).join(" · ")}. Эффект: «риск» — событие ухудшает показатели компании, «возможность» — улучшает, «отслеживать» — влияние неоднозначно или ещё не наступило.</div>
      </div>
      <div class="feedback">Полезно? <button class="btn ghost small" data-fb="1">👍</button><button class="btn ghost small" data-fb="-1">👎</button>
        <span id="fbReasons" hidden>Что не так?
          <button class="btn ghost small" data-fbr="not_relevant">Не про мой бизнес</button>
          <button class="btn ghost small" data-fbr="known">Уже знал(а)</button>
          <button class="btn ghost small" data-fbr="inaccurate">Неточный вывод</button></span><span id="fbDone"></span></div>
      ${chatBlock()}
      <div class="disclaimer">${esc(DISCLAIMER)}</div>`;
  } else if (tab === "sources") {
    body = `<ol class="sources-list">${i.sources.map((s) => `<li id="src-${CSS.escape(s.id)}"><b>[${esc(s.type === "bank" ? "Альфа-Банк" : s.type === "news" ? "Новость" : s.type === "graph" ? "Граф платежей" : s.type === "calc" ? "Расчёт" : s.type === "peers" ? "Похожие компании" : "Профиль")}]</b> ${esc(s.title)}
      ${s.date ? `<span class="muted"> · ${fmtDate(s.date)}</span>` : ""}${s.page ? `<span class="muted"> · стр. ${s.page}</span>` : ""}
      ${s.stale ? `<span class="pill mixed">материал устарел</span>` : ""}
      ${s.url ? ` <a href="${esc(s.url)}" target="_blank" rel="noopener">первоисточник ↗</a>` : ""}
      ${s.quote ? `<div class="muted small">«${esc(s.quote)}»</div>` : ""}</li>`).join("")}</ol>`;
  } else {
    body = `<div id="traceBox" class="loading">Загрузка трассировки…</div>`;
    loadTrace(i.trace_id);
  }
  $("#insightPanel").innerHTML = `<div class="insight">
    <div><span class="pill ${i.effect}">${EFFECT[i.effect]}</span>
      <span class="muted small">${i.generated_by.startsWith("llm") ? "Разбор подготовлен ИИ и проверен на ошибки" : "Быстрый разбор по профилю компании"} · ${allOk ? "<span class='ok'>✓ самопроверка пройдена</span>" : "<span title='Подробности — в блоке «На чём основан вывод»'>самопроверка: есть замечания</span>"}</span>
      <span class="ins-actions"><button class="btn ghost small" id="pdfBtn" title="Сохранить разбор в PDF через печать браузера">Скачать PDF</button><button class="btn ghost small" id="refreshBtn">Пересчитать</button></span></div>
    <h2 style="margin-top:8px">${esc(i.headline)}</h2>
    ${shortBlock(i)}
    ${divergenceBlock(i)}
    ${state.detail ? `
    ${i.data_gaps.length ? `<div class="notice">Учтены пробелы в данных: ${esc(i.data_gaps.join("; "))}</div>` : ""}
    ${(() => { const f = state.feed.find((x) => x.event_id === state.current); return f && f.story ? `<details class="story" open><summary>Развитие сюжета · ${f.story.count} ${plural(f.story.count, "событие", "события", "событий")}</summary>${storyLine(f.story, state.current)}</details>` : ""; })()}
    <div class="tabs" role="tablist">${tabs.map(([k, t]) => `<button class="tab ${k === tab ? "active" : ""}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${t}</button>`).join("")}</div>
    ${body}<button class="btn ghost detail-toggle" id="detailToggle">Свернуть подробный разбор ▴</button>` : `
    <div class="cta"><button class="btn small" data-act="manager">Обсудить с менеджером</button><button class="btn ghost small" data-act="callback">Заказать обратный звонок</button></div>
    <button class="btn detail-toggle" id="detailToggle">Подробный разбор: карта влияния, сценарии, позиция банка, источники ▾</button>
    <div class="disclaimer">${esc(DISCLAIMER)}</div>`}</div>`;
  $("#detailToggle").onclick = () => { state.detail = !state.detail; track(state.detail ? "detail_open" : "detail_close", { event_id: state.current }); renderInsight("analysis"); if (!state.detail) $("#insightCol").scrollTop = 0; };
  document.querySelectorAll("[data-tab]").forEach((el) => el.onclick = () => renderInsight(el.dataset.tab));
  bindImpactMap($("#insightPanel"));
  const wi = document.getElementById("whatIf");
  if (wi) wi.addEventListener("toggle", () => { if (wi.open && !wi.dataset.ready) { wi.dataset.ready = "1"; initWhatIf(); track("whatif_open", { event_id: state.current }); } });
  document.querySelectorAll("[data-story-ev]").forEach((el) => el.onclick = () => { if (el.dataset.storyEv !== state.current) openInsight(el.dataset.storyEv, false, "story"); });
  document.querySelectorAll("#insightPanel a[target=_blank]").forEach((a) => a.addEventListener("click", () => track("source_click", { event_id: state.current })));
  const svgMap = document.querySelector("#insightPanel .im-svg");
  if (svgMap) svgMap.addEventListener("mouseenter", () => track("map_hover", { event_id: state.current }), { once: true });
  document.querySelectorAll("[data-src]").forEach((el) => el.onclick = () => { renderInsight("sources"); const li = document.getElementById("src-" + CSS.escape(el.dataset.src)); if (li) { li.classList.add("hl"); li.scrollIntoView({ block: "center" }); } });
  $("#refreshBtn").onclick = () => openInsight(state.current, true);
  $("#pdfBtn").onclick = () => printDoc("insight");
  document.querySelectorAll("[data-fb]").forEach((el) => el.onclick = () => +el.dataset.fb > 0 ? sendFeedback(1) : ($("#fbReasons").hidden = false));
  document.querySelectorAll("[data-fbr]").forEach((el) => el.onclick = () => sendFeedback(-1, el.dataset.fbr));
  document.querySelectorAll("[data-act]").forEach((el) => el.onclick = () => openAction(el.dataset.act, el.dataset.product));
  bindChat();
}

async function loadTrace(traceId) {
  const { steps } = await api(`/api/trace/${traceId}`);
  $("#traceBox").classList.remove("loading");
  $("#traceBox").innerHTML = `<p class="muted small">Как агент пришёл к выводу: какие данные он взял, какие модели и проверки участвовали, сколько это заняло и стоило.</p>`
    + traceTimeline(steps) + `<details class="trace-raw"><summary>Технические детали: вход и выход каждого шага</summary>` + steps.map((s) => `<div class="trace-step">
    <div><span class="k">${esc(s.kind)}</span> <b>${s.step_no}. ${esc(s.name)}</b>
    <span class="muted small">${s.model ? "· " + esc(s.model) : ""} · ${s.duration_ms} мс${s.tokens_in ? ` · ${s.tokens_in}/${s.tokens_out} ток. · ${(+s.cost_rub).toFixed(3)} ₽` : ""}</span></div>
    <details><summary class="small muted">вход/выход</summary><pre>${esc(JSON.stringify({ input: s.input, output: s.output }, null, 2))}</pre></details></div>`).join("") + `</details>`;
}

// ---------------------------------------------------------------- чат
// Ответ модели — безопасный мини-markdown: сначала экранируем, потом **жирный**, списки, абзацы.
// Ссылки [news:..], [bank:..] превращаются в компактные метки с подсказкой (и ссылкой на первоисточник, если есть).
const CITE_KIND = { news: "новость", bank: "банк", forecast: "прогноз банка", profile: "профиль", graph: "контрагенты", insight: "разбор", calc: "расчёт", doc: "документ банка" };
function citeChip(id) {
  const kind = id.split(":")[0];
  const src = (state.insight?.sources || []).find((s) => s.id === id);
  const label = CITE_KIND[kind] || kind;
  const title = esc(src ? src.title : id);
  return src?.url
    ? `<a class="cite ${esc(kind)}" href="${esc(src.url)}" target="_blank" rel="noopener" title="${title}">${label}</a>`
    : `<span class="cite ${esc(kind)}" title="${title}">${label}</span>`;
}
function renderChatMd(raw) {
  const inline = (t) => esc(t)
    .replace(/[\[(]((?:[a-z_]+:[^\])\s,]+)(?:\s*,\s*[a-z_]+:[^\])\s,]+)*)[\])]/g, (_, ids) => ids.split(/\s*,\s*/).map(citeChip).join(""))
    .replace(/\b(?:news|bank|graph|profile|calc|peers|forecast|doc|insight):[^\s,;<]+/g, "")
    .replace(/\*\*([^*]+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\n]+?)\*(?=[\s.,;:!?)]|$)/g, "$1<i>$2</i>")
    .replace(/^(?:<b>)?(Позиция Альфа-Банка|Вывод сервиса(?: \(AI\))?)(?:\s*[:—-])?\s*(?:<\/b>)?\s*/, (_, l) => `<span class="lbl ${l.startsWith("Позиция") ? "bank" : "ai"}">${l}</span> `);
  const out = []; let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of raw.replace(/\r/g, "").split("\n")) {
    const t = line.trim();
    let m;
    if (!t) { close(); continue; }
    if ((m = t.match(/^(?:[-*•])\s+(.*)$/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = t.match(/^\d+[.)]\s+(.*)$/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    close();
    out.push(`<p>${inline(t.replace(/^#{1,6}\s+/, ""))}</p>`);
  }
  close();
  return out.join("");
}
function chatBlock() {
  return `<div class="chat"><h4>Задать уточняющий вопрос</h4>
    <div class="quick">${["Какие главные риски?", "Что говорит банк?", "Покажи разрез по контрагентам", "Что делать в первую очередь?", "Откуда эти выводы?"].map((t) => `<button data-q="${t}">${t}</button>`).join("")}</div>
    <div class="chat-log" id="chatLog"></div>
    <div class="chat-row"><input id="chatInput" aria-label="Ваш вопрос по разбору" placeholder="Например: как это повлияет на наш лизинг?"><button class="btn" id="chatSend">Спросить</button></div></div>`;
}
function bindChat() {
  if (!$("#chatSend")) return;
  const send = async (text) => {
    if (!text) return;
    track("chat_question", { event_id: state.current });
    $("#chatLog").insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div>`);
    $("#chatInput").value = "";
    $("#chatLog").insertAdjacentHTML("beforeend", `<div class="msg bot typing"></div>`);
    const bubble = [...document.querySelectorAll("#chatLog .msg.bot")].pop();
    let raw = "";
    try {
      await sse("/api/chat/stream", { method: "POST", body: JSON.stringify({ event_id: state.current, company_id: state.company, role: state.role, question: text }) }, (e) => {
        if (e.type === "delta") { bubble.classList.remove("typing"); raw += e.text; bubble.innerHTML = renderChatMd(raw); }
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

// ---------------------------------------------------------------- действия после инсайта (менеджер, звонок, заявка)
const ACT_TITLE = { manager: "Обсудить с менеджером", callback: "Заказать обратный звонок", application: "Оставить заявку" };
function openAction(kind, product) {
  track("action_open", { event_id: state.current, meta: { kind } });
  const d = $("#actionDialog");
  d.innerHTML = `<form method="dialog" class="act-form"><h3>${esc(ACT_TITLE[kind])}${product ? ": " + esc(product) : ""}</h3>
    <p class="muted small">Менеджер увидит, по какому событию вы обращаетесь${state.insight ? ": «" + esc(state.insight.headline) + "»" : ""}.</p>
    <textarea id="actComment" rows="4" maxlength="1000" placeholder="Комментарий (необязательно): удобное время, вопрос, сумма…"></textarea>
    <div class="row"><button value="cancel" class="btn ghost">Отмена</button><button class="btn" id="actSend" value="send">Отправить</button></div></form>`;
  d.showModal();
  $("#actSend").onclick = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/actions", { method: "POST", body: JSON.stringify({ company_id: state.company, event_id: state.current, kind, product: product || null, comment: $("#actComment").value.trim() || null }) });
      d.close(); toast(`${r.title}: запрос ${r.ticket} принят. ${r.message}`);
    } catch (err) { toast("Не удалось отправить: " + err.message); }
  };
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
          <p><button type="button" class="em-btn btn" id="digestOpen">Открыть Бизнес-радар</button></p>
          <p class="muted small">Это демонстрация: письмо не отправляется.</p></div>
      </div>
      ${pushCard()}
      <div class="card push"><div class="muted small">Предпросмотр push</div>
        <div class="push-n"><b>Альфа-Бизнес</b><div>${esc(d.top[0] ? `${EFFECT[d.top[0].effect][0].toUpperCase() + EFFECT[d.top[0].effect].slice(1)}: ${d.top[0].title}` : "Важных событий нет")}</div></div></div>
    </div>`;
  bindCards(box);
  const open = document.getElementById("digestOpen");
  if (open) open.onclick = () => show("radar");
  bindPush();
}

// Названия проверок — словами клиента; служебные ссылки в пояснениях не показываем
const CHECK_RU = { "Ответ на 6 вопросов": "Есть ответы на все 6 вопросов", "Персонализация (не пересказ)": "Вывод опирается на данные компании, а не пересказывает новость",
  "Нет следов prompt injection": "Текст новости не пытался вмешаться в работу сервиса", "Ссылки на источники корректны": "Все ссылки ведут на реальные источники" };
function cleanRefs(t) { return String(t || "").replace(/\b(?:news|bank|graph|profile|calc|peers):[^\s,;)]+/g, "источник").replace(/(источник)(,\s*источник)+/g, "$1"); }

// ---------------------------------------------------------------- PDF: печать браузера со своими стилями
function printDoc(kind) {
  const c = $("#companySelect").selectedOptions[0], r = $("#roleSelect").selectedOptions[0];
  $("#printMeta").textContent = [c ? c.textContent : "", r && r.value ? r.textContent : "", new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" })].filter(Boolean).join(" · ");
  const wasDetail = state.detail;
  if (kind === "insight" && !state.detail) { state.detail = true; renderInsight("analysis"); }
  document.body.dataset.print = kind;
  track("pdf_export", { event_id: kind === "insight" ? state.current : null, meta: { kind } });
  const done = () => { delete document.body.dataset.print; if (kind === "insight" && !wasDetail) { state.detail = false; renderInsight("analysis"); } removeEventListener("afterprint", done); };
  addEventListener("afterprint", done);
  setTimeout(() => window.print(), 60);
}

// ---------------------------------------------------------------- push в браузере
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
function pushCard() {
  if (!pushSupported()) return `<div class="card notif"><b>Уведомления в браузере</b><div class="muted small">Этот браузер не поддерживает push. Откройте радар в Chrome, Edge или Firefox.</div></div>`;
  return `<div class="card notif"><b>Уведомления в браузере</b>
    <div class="muted small">Новый риск высокой важности — сразу, дайджест — по понедельникам. Только для выбранной компании и роли.</div>
    <div class="notif-row" id="pushRow"><span class="muted small">Проверяем…</span></div></div>`;
}
function urlB64ToUint8(s) { const b = atob((s + "=".repeat((4 - s.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(b, (c) => c.charCodeAt(0)); }
async function pushSubscription() {
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  return { reg, sub: await reg.pushManager.getSubscription() };
}
async function bindPush() {
  const row = document.getElementById("pushRow");
  if (!row) return;
  const render = async () => {
    let sub = null;
    try { sub = (await pushSubscription()).sub; } catch (e) { row.innerHTML = `<span class="muted small">Не удалось запустить уведомления: ${esc(e.message)}</span>`; return; }
    if (Notification.permission === "denied") { row.innerHTML = `<span class="muted small">Уведомления запрещены в настройках браузера для этого сайта — разрешите их и обновите страницу.</span>`; return; }
    row.innerHTML = sub ? `<span class="ok small">✓ Включены</span><button class="btn small" id="pushTest">Отправить тестовое</button><button class="btn ghost small" id="pushOff">Отключить</button>`
      : `<button class="btn small" id="pushOn">Включить уведомления</button>`;
    const on = document.getElementById("pushOn"), test = document.getElementById("pushTest"), off = document.getElementById("pushOff");
    if (on) on.onclick = async () => {
      try {
        if ((await Notification.requestPermission()) !== "granted") { toast("Без разрешения браузера уведомления не придут"); return render(); }
        const { reg } = await pushSubscription();
        const { public_key } = await api("/api/push/key");
        const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8(public_key) });
        await api("/api/push/subscribe", { method: "POST", body: JSON.stringify({ company_id: state.company, role: state.role, subscription: s.toJSON() }) });
        track("push_subscribe"); toast("Уведомления включены"); render();
      } catch (e) { toast("Не удалось включить: " + e.message); }
    };
    if (test) test.onclick = async () => { try { await api("/api/push/test", { method: "POST" }); toast("Отправили — уведомление появится через пару секунд"); } catch (e) { toast(e.message); } };
    if (off) off.onclick = async () => {
      const { sub: s } = await pushSubscription();
      if (s) { await api("/api/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: s.endpoint }) }); await s.unsubscribe(); }
      toast("Уведомления отключены"); render();
    };
  };
  render();
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
const OFFLINE = [  // те же цели, что в scripts/quality_gate.py
  ["relevance.p3", "Лента: P@3 — в топ-3 только релевантное", 0.90],
  ["relevance.ndcg5", "Лента: nDCG@5 — порядок по важности", 0.85],
  ["relevance.critical_recall10", "Лента: критичное попало в топ-10", 0.85],
  ["retrieval_recall5", "Позиция банка находится (recall@5)", 0.85],
  ["classification.f1", "Классификация новостей, F1", 0.80],
  ["classification.direction_acc", "Направление влияния (+/−)", 0.80],
  ["entities.industries.f1", "Отрасли в новости, F1", 0.90],
  ["entities.regions.f1", "Регионы в новости, F1", 0.90],
  ["entities.noise_accuracy", "Отсев шума", 0.95],
  ["dedup.precision", "Склейка дублей: precision", 0.90],
  ["dedup.recall", "Склейка дублей: recall", 0.85],
  ["graph_share", "Влияние объяснено через граф связей", 0.30],
  ["selfcheck_passed", "Разборы прошли самопроверку", 1.0],
];
const AXES = { personalization: "Персонализация", depth: "Глубина", grounding: "Обоснованность", bank_view: "Позиция банка", actionability: "Конкретность" };
const FB_REASONS = { not_relevant: "не про мой бизнес", known: "уже знал", inaccurate: "неточный вывод" };
const ACTION_NAMES = { manager: "менеджер", callback: "звонок", application: "заявка" };
const pct = (v) => v == null ? "—" : `${Math.round(v * 100)}%`;
const num = (v, d = 2) => v == null ? "—" : String(Math.round(v * 10 ** d) / 10 ** d).replace(".", ",");
const getPath = (o, p) => p.split(".").reduce((x, k) => (x && typeof x === "object" ? x[k] : undefined), o);
const secs = (ms) => ms == null ? "—" : ms < 1000 ? `${ms} мс` : `${num(ms / 1000, 1)} с`;
state.metricsDays = 7;

async function loadMetrics() {
  const seg = $("#metricsDays");
  if (!seg.dataset.bound) {
    seg.dataset.bound = "1";
    seg.querySelectorAll("button").forEach((b) => b.onclick = () => {
      state.metricsDays = +b.dataset.d;
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      loadMetrics();
    });
  }
  flushTrack();
  const m = await api(`/api/metrics?days=${state.metricsDays}`);
  const p = m.product, r = p.rates, f = p.funnel, sp = p.speed_ms, c = m.cost, j = m.insights.judge || {};
  const tile = (t, v, s = "", cls = "") => `<div class="card metric ${cls}"><div class="muted small">${t}</div><div class="v">${v}</div><div class="muted small">${s}</div></div>`;
  const vsTarget = (v, target, lowerBetter = false) => v == null ? "" : (lowerBetter ? v <= target : v >= target) ? "good" : "warn";
  const alerts = m.alerts.length
    ? m.alerts.map((a) => `<div class="alert ${a.level}">${a.level === "warn" ? "⚠" : "ℹ"} ${esc(a.text)}</div>`).join("")
    : `<div class="alert ok">✓ Все показатели в норме: бюджет, скорость, источники, обратная связь, безопасность</div>`;
  const actionsTotal = Object.values(p.actions).reduce((a, b) => a + b, 0);
  const steps = [["Открыли главную", f.home_views], ["Перешли из виджета", f.widget_clicks], ["Открыли ленту", f.feed_views],
    ["Открыли разбор", f.card_opens], ["Проверили первоисточник", p.counts.source_click || 0], ["Обратились в банк после разбора", actionsTotal]];
  const maxStep = Math.max(1, ...steps.map((s) => s[1]));
  const funnel = steps.map(([t, n]) => `<div class="fn-row"><span>${t}</span><div class="fn-bar"><i style="width:${n ? Math.max(2, n / maxStep * 100) : 0}%"></i></div><b>${n}</b></div>`).join("");
  const reasons = Object.entries(p.feedback.neg_reasons);
  const offline = m.offline ? OFFLINE.map(([path, title, target]) => {
    const v = getPath(m.offline, path);
    if (v == null) return "";
    return `<tr><td>${title}</td><td class="num"><b>${num(v, 3)}</b></td><td class="num muted">≥ ${num(target)}</td><td>${v >= target ? '<span class="status ok">● в цели</span>' : '<span class="status mid">● ниже цели</span>'}</td></tr>`;
  }).join("") : "";
  const rel = (m.offline || {}).relevance || {};
  const dd = (m.offline || {}).dedup || {};
  $("#metricsBox").innerHTML = `
    <div class="alerts">${alerts}</div>

    <h3 class="mon-h">Продукт · ${({ 1: "сутки", 7: "7 дней", 30: "30 дней" })[state.metricsDays]}</h3>
    <div class="metrics">
      ${tile("Активные пользователи", p.active_users)}
      ${tile("CTR виджета на главной", pct(r.widget_ctr), "клики по карточкам / показы главной", vsTarget(r.widget_ctr, 0.15))}
      ${tile("Открывают разбор из ленты", pct(r.feed_open_rate), "открытия / заходы в ленту", vsTarget(r.feed_open_rate, 0.3))}
      ${tile("Полезно 👍", pct(r.helpful_share), `👍 ${p.feedback.pos} · 👎 ${p.feedback.neg}${reasons.length ? " · " + reasons.map(([k, v]) => `${FB_REASONS[k] || esc(k)}: ${v}`).join(", ") : ""}`, vsTarget(r.helpful_share, 0.8))}
      ${tile("Проверяют первоисточник", pct(r.source_ctr), "доля разборов с переходом к источнику")}
      ${tile("Вопросы в чате", num(r.chat_per_insight), "на один открытый разбор")}
      ${tile("Сценарий «что если»", pct(r.whatif_rate), "доля разборов, где открыли сценарий")}
      ${tile("Обращаются в банк", pct(r.action_rate), Object.entries(p.actions).map(([k, v]) => `${ACTION_NAMES[k] || esc(k)}: ${v}`).join(" · ") || "менеджер, звонок, заявка")}
      ${tile("Скрывают как нерелевантное", pct(r.hide_rate), "сигнал пересмотреть ранжирование · цель ≤ 10%", vsTarget(r.hide_rate, 0.1, true))}
      ${tile("Тур для жюри пройден", pct(r.tour_completion), `${p.counts.tour_start || 0} запусков`)}
    </div>
    <div class="card funnel"><div class="muted small">Воронка: от главной до обращения в банк</div>${funnel}</div>

    <h3 class="mon-h">Скорость и стоимость разбора</h3>
    <div class="metrics">
      ${tile("Первый вывод, p50 / p95", `${secs(sp.preview_p50)} / ${secs(sp.preview_p95)}`, "предпросмотр без модели · цель p95 ≤ 2 с", vsTarget(sp.preview_p95, 2000, true))}
      ${tile("Полный разбор моделью, p50 / p95", `${secs(sp.full_fresh_p50)} / ${secs(sp.full_fresh_p95)}`, "план и разбор события параллельно → черновик → критик → правка при необходимости · цель p95 ≤ 60 с", vsTarget(sp.full_fresh_p95, 60000, true))}
      ${tile("Повторное открытие, p50", secs(sp.full_cached_p50), "из кэша разборов")}
      ${tile("Стоимость разбора", c.avg_rub != null ? `${num(c.avg_rub)} ₽` : "—", c.avg_rub != null ? `p95 ${num(c.p95_rub)} ₽ · ${num(c.avg_llm_calls, 1)} вызовов модели · ${num(c.avg_build_s, 1)} с · цель в среднем ≤ 2 ₽, потолок 4 ₽` : "появится после разборов моделью", vsTarget(c.avg_rub, 2, true))}
      ${tile("Бюджет LLM сегодня", `${num(m.llm.spent_rub)} ₽`, `из ${m.llm.budget_rub} ₽ · фоновые задачи до ${m.llm.background_limit_rub} ₽ · режим ${esc(m.llm.mode)}`, m.llm.budget_rub && m.llm.spent_rub / m.llm.budget_rub >= 0.8 ? "warn" : "good")}
      ${tile("Разборов построено", m.insights.total, `${c.with_llm} — моделью, остальные — правилами`)}
    </div>

    <h3 class="mon-h">Качество разборов: LLM-судья (1–5)</h3>
    <div class="metrics">
      ${tile("Средняя оценка", j.avg != null ? num(j.avg) : "—", j.n ? `${j.n} разборов · цель ≥ 4,2` : "появится после разборов моделью", vsTarget(j.avg, 4.2))}
      ${Object.entries(j.by_axis || {}).map(([k, v]) => tile(AXES[k] || esc(k), num(v), "", vsTarget(v, 4))).join("")}
      ${tile("Прошли самопроверку", pct(m.insights.quality_passed_share), "факты, цифры, источники, дисклеймер", vsTarget(m.insights.quality_passed_share, 0.95))}
    </div>

    <h3 class="mon-h">Офлайн-оценка на размеченных данных</h3>
    <div class="card"><div class="muted small" style="margin-bottom:8px">${rel.pairs ? `${rel.pairs} пар «профиль клиента — новость» по ${rel.cases} профилям${dd.pairs ? `, ${dd.pairs} пар для склейки дублей` : ""}. ` : ""}Пересчитывается в CI на каждый PR; падение метрики больше чем на 0,05 блокирует слияние.</div>
      ${offline ? `<div class="table-wrap"><table class="sources"><thead><tr><th>Метрика</th><th>Значение</th><th>Цель</th><th></th></tr></thead><tbody>${offline}</tbody></table></div>` : '<div class="muted">Базовая линия не найдена</div>'}</div>

    <h3 class="mon-h">Данные</h3>
    <div class="metrics">
      ${tile("События в базе", m.events, `${m.companies} компаний`)}
      ${tile("Источники новостей", `${m.sources.total - m.sources.down} / ${m.sources.total}`, m.sources.down ? `с ошибками: ${m.sources.down}` : "все работают", m.sources.down ? "warn" : "good")}
      ${tile("Последний сбор", m.sources.last_ingest ? ago(m.sources.last_ingest.at) : "—", m.sources.last_ingest ? `новых событий ${m.sources.last_ingest.new_events}, отсеяно шума ${m.sources.last_ingest.noise}` : "")}
      ${tile("Ошибки в браузере", p.counts.js_error || 0, "за период · пишутся автоматически, без данных пользователя", (p.counts.js_error || 0) >= 5 ? "warn" : "good")}
    </div>`;
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

// ---------------------------------------------------------------- режим ИИ (для фонового реранка ленты; бюджет — на экране «Качество и бюджет»)
async function loadBudget() {
  try { state.llmMode = (await api("/api/llm/budget")).mode; } catch (e) { /* необязательно */ }
}
loadBudget();

init().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="notice">Ошибка запуска: ${esc(e.message)}</div>`); });
