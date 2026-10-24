// Этап 5: главная и навигация — сводка дня, поиск Ctrl+K, место на глобусе как фильтр ленты, мини-графики в плитках,
// настройка главной, онбординг в 3 шага, «всё прочитано», хлебные крошки, «мои компании» со счётчиками, пустые состояния.
// Функции вызываются из app.js после загрузки данных; общие помощники (state, $, esc, plural, api, show…) — оттуда.

// ---------------------------------------------------------------- пустые состояния: линейные иллюстрации в стиле банка
const ART = {
  read: `<circle cx="60" cy="52" r="30" class="a-soft"/><path d="m46 52 10 10 19-21" class="a-red" stroke-width="5"/><path d="M100 22v8M96 26h8M18 80v6M15 83h6" class="a-line"/>`,
  search: `<circle cx="54" cy="48" r="24" class="a-soft"/><circle cx="54" cy="48" r="24" class="a-line"/><path d="m72 66 18 18" class="a-red" stroke-width="6"/><path d="M44 48h20" class="a-line"/>`,
  tasks: `<rect x="34" y="18" width="52" height="66" rx="8" class="a-soft"/><rect x="34" y="18" width="52" height="66" rx="8" class="a-line"/><path d="M48 14h24v10H48z" class="a-line"/><path d="m44 44 5 5 9-9M64 45h12M44 64h32" class="a-line"/><circle cx="88" cy="78" r="11" class="a-redfill"/><path d="M88 73v10M83 78h10" stroke="#fff" stroke-width="3" fill="none"/>`,
  calendar: `<rect x="26" y="24" width="68" height="58" rx="8" class="a-soft"/><rect x="26" y="24" width="68" height="58" rx="8" class="a-line"/><path d="M26 40h68M44 16v14M76 16v14" class="a-line"/><circle cx="60" cy="61" r="8" class="a-redfill"/>`,
  insight: `<rect x="30" y="16" width="50" height="66" rx="6" class="a-soft"/><rect x="30" y="16" width="50" height="66" rx="6" class="a-line"/><path d="M40 32h30M40 44h30M40 56h18" class="a-line"/><path d="m74 56 22 10-10 3-3 10z" class="a-redfill"/>`,
  quiet: `<path d="M16 66h20l8-18 10 30 10-42 10 30h30" class="a-line"/><circle cx="64" cy="36" r="5" class="a-redfill"/>`,
};
function emptyArt(kind) { return `<svg class="e-art" viewBox="0 0 120 96" aria-hidden="true">${ART[kind] || ART.search}</svg>`; }
function emptyState(kind, title, text, cta = "") {
  return `<div class="e-state">${emptyArt(kind)}<b>${esc(title)}</b>${text ? `<p>${text}</p>` : ""}${cta}</div>`;
}

// ---------------------------------------------------------------- 41. сводка дня одной фразой (кликабельные части)
function renderDaySum() {
  const box = document.getElementById("daySum");
  if (!box || !state.feed) return;
  const read = readSet();
  const fresh = state.feed.filter(isFresh), freshRisk = fresh.filter((x) => x.effect === "risk").length;
  const unreadRisk = state.feed.filter((x) => x.effect === "risk" && !read.has(x.event_id)).length;
  const parts = [];
  if (freshRisk) parts.push(["risk", `${freshRisk} ${plural(freshRisk, "новый риск", "новых риска", "новых рисков")} за сутки`, "fresh"]);
  else if (fresh.length) parts.push(["watch", `${fresh.length} ${plural(fresh.length, "новое событие", "новых события", "новых событий")} за сутки, новых рисков нет`, "fresh"]);
  else parts.push(["ok", unreadRisk ? `за сутки нового нет · ${unreadRisk} ${plural(unreadRisk, "риск ждёт", "риска ждут", "рисков ждут")} прочтения` : "за сутки нового нет, всё прочитано", "radar"]);
  const nd = state.nextDate;
  if (nd) parts.push(["cal", `${nd.kind === "cbr" ? "заседание ЦБ" : "норма вступает в силу"} ${nd.n === 0 ? "сегодня" : `через ${nd.n} ${plural(nd.n, "день", "дня", "дней")}`}`, "calendar"]);
  const rv = (state.freshRevs || []).length;
  if (rv) parts.push(["fc", `банк пересмотрел ${rv} ${plural(rv, "прогноз", "прогноза", "прогнозов")}`, "forecasts"]);
  box.innerHTML = `<span class="ds-cap">Сегодня:</span>` + parts.map(([k, t, go]) => `<button class="ds-part ds-${k}" data-ds="${go}">${esc(t)}</button>`).join(`<span class="ds-sep" aria-hidden="true">·</span>`);
  box.querySelectorAll("[data-ds]").forEach((b) => b.onclick = () => {
    if (b.dataset.ds === "fresh") { filters.quick = new Set(["fresh"]); show("radar"); renderFeedList(); return; }
    show(b.dataset.ds);
  });
}

// ---------------------------------------------------------------- 44. мини-графики в плитках «Дела по радару»
function sparkSvg(vals, cls = "") {
  const w = 64, h = 22, max = Math.max(1, ...vals), step = w / Math.max(1, vals.length - 1);
  const pts = vals.map((v, i) => [i * step, h - 2 - (v / max) * (h - 4)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  return `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${line}L${w} ${h}L0 ${h}Z" class="sp-area"/><path d="${line}" class="sp-line"/><circle cx="${last[0]}" cy="${last[1]}" r="2.4" class="sp-dot"/></svg>`;
}
function renderSparks() {
  if (!state.feed) return;
  const days = 7, today = new Date(); today.setHours(0, 0, 0, 0);
  const byDay = (pred) => Array.from({ length: days }, (_, k) => {
    const d0 = new Date(today - (days - 1 - k) * 864e5), d1 = new Date(+d0 + 864e5);
    return state.feed.filter((x) => pred(x) && x.published_at && new Date(x.published_at) >= d0 && new Date(x.published_at) < d1).length;
  });
  const put = (sel, html, label) => {
    const tile = document.querySelector(sel); if (!tile) return;
    let s = tile.querySelector(".hs-spark");
    if (!s) { s = document.createElement("span"); s.className = "hs-spark"; tile.appendChild(s); }
    s.innerHTML = html; s.title = label;
  };
  // за неделю тихо — показываем недели (8 недель), иначе линия у нуля ничего не говорит
  let risks = byDay((x) => x.effect === "risk"), unit = "по дням за неделю";
  if (!risks.some(Boolean)) {
    unit = "по неделям за 2 месяца";
    risks = Array.from({ length: 8 }, (_, k) => { const d1 = new Date(+today + 864e5 - (7 - k) * 7 * 864e5), d0 = new Date(+d1 - 7 * 864e5);
      return state.feed.filter((x) => x.effect === "risk" && x.published_at && new Date(x.published_at) >= d0 && new Date(x.published_at) < d1).length; });
  }
  put("[data-strip=radar]", sparkSvg(risks, "risk"), `Риски ${unit}: ${risks.join(", ")}`);
  if (state.tasks && state.tasks.length) {
    const all = state.tasks.length, done = state.tasks.filter((t) => t.status !== "open").length;
    put("[data-strip=tasks]", `<span class="hs-prog" aria-hidden="true"><i style="width:${all ? Math.round(done / all * 100) : 0}%"></i></span><small>${done} из ${all} готово</small>`, `Выполнено ${done} из ${all}`);
  }
  if (state.nextDate) {
    const n = state.nextDate.n, k = Math.max(0, Math.min(1, 1 - n / 90)), r = 9, c = 2 * Math.PI * r;
    put("[data-strip=calendar]", `<svg class="spark ring" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="${r}" class="rg-bg"/><circle cx="12" cy="12" r="${r}" class="rg-fg" stroke-dasharray="${(c * k).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 12 12)"/></svg>`, `До даты ${n} дн.`);
  }
}

// ---------------------------------------------------------------- 43. место на глобусе → фильтр ленты
function filterByPlace(cluster) {
  const ids = new Set(cluster.map((x) => x.id));
  state.placeFilter = { label: cluster[0].place || "место на глобусе", ids };
  track("globe_place", { meta: { n: ids.size } });
  show("radar"); renderFeedList(); renderPlaceChip();
}
function renderPlaceChip() {
  const box = document.getElementById("placeChip");
  if (!box) return;
  const pf = state.placeFilter;
  box.hidden = !pf;
  if (!pf) return;
  box.innerHTML = `${ico("pin")} <span>${esc(pf.label)} · ${pf.ids.size} ${plural(pf.ids.size, "событие", "события", "событий")} с глобуса</span><button class="link-btn" id="placeOff" aria-label="Снять фильтр по месту">×</button>`;
  document.getElementById("placeOff").onclick = () => { state.placeFilter = null; renderPlaceChip(); renderFeedList(); };
}

// ---------------------------------------------------------------- 42. поиск по всему сервису (Ctrl+K)
const CMD_ACTIONS = [
  ["Проверить новость по ссылке или тексту", "analyze", () => $("#analyzeDialog").showModal()],
  ["Обсудить с менеджером банка", "chat", () => { state.current = null; openAction("manager"); }],
  ["Мои цифры: долг, ставка, ФОТ", "calc", () => openNumbers()],
  ["Настроить главную", "grid", () => openHomeSetup()],
  ["Тур по сервису", "play", () => startTour()],
  ["Светлая / тёмная тема", "moon", () => toggleTheme()],
];
const norm = (s) => String(s || "").toLowerCase().replace(/ё/g, "е");
function cmdItems(qs) {
  const t = norm(qs).trim(), words = t.split(/\s+/).filter(Boolean);
  const hit = (s) => words.every((w) => norm(s).includes(w));
  const views = [...document.querySelectorAll(".sidebar .nav-item[data-view]")].map((n) => ({
    group: "Разделы", title: n.querySelector("span")?.textContent || n.textContent.trim(), run: () => show(n.dataset.view, n.dataset.bank) }));
  const acts = CMD_ACTIONS.map(([title, , run]) => ({ group: "Действия", title, run }));
  const evs = (state.feed || []).map((it) => ({ group: "События ленты", title: it.title, sub: `${EFFECT[it.effect]} · ${fmtDate(it.published_at)}`,
    pill: it.effect, run: () => { show("radar"); openInsight(it.event_id, false, "search"); } }));
  const all = [...views, ...acts, ...evs];
  if (!words.length) return [...views.slice(0, 5), ...acts.slice(0, 4), ...evs.slice(0, 4)];
  return all.filter((x) => hit(x.title + " " + (x.sub || ""))).slice(0, 14);
}
function openCmd() {
  let d = document.getElementById("cmdk");
  if (!d) {
    d = document.createElement("dialog"); d.id = "cmdk"; d.setAttribute("aria-label", "Поиск по сервису");
    d.innerHTML = `<div class="cmd"><label class="cmd-in">${ico("search")}<input id="cmdInput" type="text" placeholder="Событие, раздел, действие или вопрос к аналитике банка" autocomplete="off" aria-controls="cmdList"><kbd>Esc</kbd></label>
      <ul class="cmd-list" id="cmdList" role="listbox"></ul><div class="cmd-bank" id="cmdBank"></div>
      <div class="cmd-foot muted small"><span><kbd>↑</kbd><kbd>↓</kbd> выбрать</span><span><kbd>Enter</kbd> открыть</span><span><kbd>Ctrl</kbd>+<kbd>K</kbd> в любом месте</span></div></div>`;
    document.body.appendChild(d);
    d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
    const inp = d.querySelector("#cmdInput");
    let timer = 0;
    inp.oninput = () => { state.cmdSel = 0; drawCmd(); clearTimeout(timer); timer = setTimeout(() => cmdBank(inp.value), 350); };
    inp.onkeydown = (e) => {
      const n = (state.cmdList || []).length;
      if (e.key === "ArrowDown") { e.preventDefault(); state.cmdSel = (state.cmdSel + 1) % Math.max(1, n); drawCmd(false); }
      else if (e.key === "ArrowUp") { e.preventDefault(); state.cmdSel = (state.cmdSel - 1 + n) % Math.max(1, n); drawCmd(false); }
      else if (e.key === "Enter") { e.preventDefault(); runCmd(state.cmdSel); }
      else if (e.key === "Escape") { e.preventDefault(); d.close(); }
    };
  }
  state.cmdSel = 0;
  d.querySelector("#cmdInput").value = "";
  d.querySelector("#cmdBank").innerHTML = "";
  drawCmd();
  d.showModal();
  d.querySelector("#cmdInput").focus();
  track("cmd_open");
}
function drawCmd(rebuild = true) {
  const inp = document.getElementById("cmdInput"), ul = document.getElementById("cmdList");
  if (rebuild) state.cmdList = cmdItems(inp.value);
  const list = state.cmdList;
  let g = "";
  ul.innerHTML = list.map((x, k) => { const head = x.group !== g ? `<li class="cmd-g" role="presentation">${esc(x.group)}</li>` : ""; g = x.group;
    return `${head}<li role="option" class="cmd-it ${k === state.cmdSel ? "on" : ""}" aria-selected="${k === state.cmdSel}" data-k="${k}">
      ${x.pill ? `<span class="dot eff-dot ${x.pill}"></span>` : ""}<span class="cmd-t">${esc(x.title)}</span>${x.sub ? `<span class="muted small">${esc(x.sub)}</span>` : ""}</li>`; }).join("")
    || `<li class="cmd-none">${emptyState("search", "Ничего не нашлось", "Попробуйте другое слово — или посмотрите ответ из материалов банка ниже.")}</li>`;
  ul.querySelectorAll("[data-k]").forEach((li) => { li.onclick = () => runCmd(+li.dataset.k); li.onmousemove = () => { if (state.cmdSel !== +li.dataset.k) { state.cmdSel = +li.dataset.k; drawCmd(false); } }; });
  const on = ul.querySelector(".on"); if (on) on.scrollIntoView({ block: "nearest" });
}
function runCmd(k) {
  const x = (state.cmdList || [])[k];
  if (!x) return;
  document.getElementById("cmdk").close();
  track("cmd_run", { meta: { group: x.group } });
  x.run();
}
async function cmdBank(qs) {
  const box = document.getElementById("cmdBank");
  if (!box) return;
  if (norm(qs).trim().length < 3) { box.innerHTML = ""; return; }
  try {
    const r = await api(`/api/bank/search?${q({ q: qs })}`);
    if (document.getElementById("cmdInput").value !== qs) return;
    const top = (r.results || []).slice(0, 2);
    box.innerHTML = top.length ? `<div class="cmd-g">Аналитика Альфа-Банка</div>${top.map((x) => `<button class="cmd-bk" data-doc="${esc(x.doc_id)}"><span>${esc(x.text)}</span>
      <span class="muted small">${esc(x.doc_title || "")}${x.page ? `, стр. ${x.page}` : ""} · ${fmtDate(x.date)}</span></button>`).join("")}` : "";
    box.querySelectorAll("[data-doc]").forEach((b) => b.onclick = () => { document.getElementById("cmdk").close(); openDoc(b.dataset.doc); });
  } catch (e) { box.innerHTML = ""; }
}
function bindCmd() {
  document.addEventListener("keydown", (e) => {
    if (document.body.classList.contains("logged-out")) return;
    const typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable]");
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K" || e.code === "KeyK")) { e.preventDefault(); openCmd(); }
    else if (e.key === "/" && !typing && !document.querySelector("dialog[open]")) { e.preventDefault(); openCmd(); }
  });
  const b = document.getElementById("cmdBtn");
  if (b) b.onclick = openCmd;
}

// ---------------------------------------------------------------- 45. настройка главной: скрыть и переставить карточки
const HOME_CARDS = { focusCard: "Главное сейчас", radarWidget: "Что важно сегодня", moneyCard: "Деньги на кону", bankCard: "Макропрогноз банка",
  pulseCard: "Пульс ленты", homeWhatIf: "Что если", bankThinks: "Что думает банк", datesMini: "Ближайшие даты", tasksMini: "Мои действия",
  radarCard: "Что давит на бизнес", profileCard: "Профиль и интересы" };
function homePrefs() { try { return JSON.parse(localStorage.getItem("bn:home") || "null") || {}; } catch (e) { return {}; } }
function saveHomePrefs(p) { try { localStorage.setItem("bn:home", JSON.stringify(p)); } catch (e) { /* без хранилища — до перезагрузки */ } }
function applyHomeLayout() {
  const p = homePrefs(), hidden = new Set(p.hidden || []);
  document.querySelectorAll(".home-grid .home-col").forEach((col, ci) => {
    (p.order && p.order[ci] || []).forEach((id) => { const el = document.getElementById(id); if (el && el.parentElement === col) col.appendChild(el); });
  });
  Object.keys(HOME_CARDS).forEach((id) => { const el = document.getElementById(id); if (el) el.classList.toggle("home-off", hidden.has(id)); });
}
function openHomeSetup() {
  const d = $("#actionDialog");
  const draw = () => {
    const p = homePrefs(), hidden = new Set(p.hidden || []);
    const cols = [...document.querySelectorAll(".home-grid .home-col")].map((col) => [...col.children].map((el) => el.id).filter((id) => HOME_CARDS[id]));
    d.innerHTML = `<form method="dialog" class="act-form hs-setup"><h3>Настроить главную</h3>
      <p class="muted small">Выключите лишнее и поставьте важное выше. Настройка хранится в этом браузере.</p>
      ${cols.map((ids, ci) => `<div class="sub-h">${ci ? "Правая колонка" : "Левая колонка"}</div><ul class="hs-list">${ids.map((id, k) => `<li class="${hidden.has(id) ? "off" : ""}">
        <label class="chk"><input type="checkbox" data-hc="${id}" ${hidden.has(id) ? "" : "checked"}> ${esc(HOME_CARDS[id])}</label>
        <span class="hs-mv"><button type="button" class="icon-btn sm" data-mv="${ci}:${k}:-1" ${k ? "" : "disabled"} aria-label="Выше">↑</button><button type="button" class="icon-btn sm" data-mv="${ci}:${k}:1" ${k < ids.length - 1 ? "" : "disabled"} aria-label="Ниже">↓</button></span></li>`).join("")}</ul>`).join("")}
      <div class="row"><button type="button" class="btn ghost" id="hsReset">Как было</button><button class="btn" value="ok">Готово</button></div></form>`;
    d.querySelectorAll("[data-hc]").forEach((c) => c.onchange = () => {
      const p2 = homePrefs(), h = new Set(p2.hidden || []);
      if (c.checked) h.delete(c.dataset.hc); else h.add(c.dataset.hc);
      p2.hidden = [...h]; saveHomePrefs(p2); applyHomeLayout(); draw();
    });
    d.querySelectorAll("[data-mv]").forEach((b) => b.onclick = () => {
      const [ci, k, dir] = b.dataset.mv.split(":").map(Number), ids = cols[ci];
      [ids[k], ids[k + dir]] = [ids[k + dir], ids[k]];
      const p2 = homePrefs(); p2.order = p2.order || {}; p2.order[ci] = ids; saveHomePrefs(p2); applyHomeLayout(); draw();
    });
    d.querySelector("#hsReset").onclick = () => { saveHomePrefs({}); location.reload(); };
  };
  draw();
  if (!d.open) d.showModal();
  track("home_setup");
}

// ---------------------------------------------------------------- 49. «мои компании»: риски и новое у каждой компании
async function loadCompanyOverview() {
  const sel = document.getElementById("companySelect");
  if (!sel || sel.options.length < 2) return;
  try {
    const r = await api(`/api/me/overview?${q({ role: state.role })}`);
    const by = Object.fromEntries(r.companies.map((c) => [c.uid, c]));
    [...sel.options].forEach((o) => {
      if (!o.dataset.name) o.dataset.name = o.textContent;
      const c = by[o.value];
      o.textContent = c ? `${o.dataset.name} · ${c.risks ? `${c.risks} ${plural(c.risks, "риск", "риска", "рисков")}` : "без рисков"}${c.fresh ? `, новых ${c.fresh}` : ""}` : o.dataset.name;
    });
    const other = r.companies.filter((c) => c.uid !== state.company && c.fresh).length;
    const sw = document.querySelector(".sw-company");
    if (sw) { sw.classList.toggle("has-new", other > 0); sw.title = other ? `В других компаниях есть новые события: ${other}` : ""; }
    ctxLabel();
  } catch (e) { /* без сводки — обычный список компаний */ }
}

// ---------------------------------------------------------------- 48. хлебные крошки в разборе
const FROM_RU = { home: "Главная", globe: "Главная · глобус", bell: "Уведомления", search: "Поиск", calendar: "Календарь", push: "Уведомление", tasks: "Мои действия" };
function crumbsHtml(title) {
  const from = state.insightFrom || "feed";
  const mid = FROM_RU[from];
  const back = ["home", "globe"].includes(from) ? "home" : from === "calendar" ? "calendar" : from === "tasks" ? "tasks" : "";
  return `<nav class="crumbs" aria-label="Где вы"><button class="link-btn" data-crumb="home">Главная</button><span aria-hidden="true">›</span>
    ${mid && back && back !== "home" ? `<button class="link-btn" data-crumb="${back}">${esc(mid)}</button><span aria-hidden="true">›</span>` : ""}
    <button class="link-btn" data-crumb="radar">Лента</button><span aria-hidden="true">›</span><span class="cr-now">${esc(title.length > 60 ? title.slice(0, 60) + "…" : title)}</span>
    ${back ? `<button class="btn ghost small cr-back" data-crumb="${back}">← ${back === "home" ? "На главную" : "Назад"}</button>` : ""}</nav>`;
}
function bindCrumbs(root) {
  root.querySelectorAll("[data-crumb]").forEach((b) => b.onclick = () => {
    const v = b.dataset.crumb;
    if (v === "radar") { if (document.body.classList.contains("m-detail")) closeDetail(true); show("radar"); return; }
    show(v);
  });
}

// ---------------------------------------------------------------- 46. онбординг в 3 шага: компания → роль → мои цифры
function onboardSteps() {
  const d = document.getElementById("onboard");
  const top = (state.exposures || []).filter((e) => e.weight > 0).slice(0, 5);
  const dots = (k) => `<div class="ob-dots" aria-label="Шаг ${k + 1} из 3">${[0, 1, 2].map((i) => `<i class="${i <= k ? "on" : ""}"></i>`).join("")}</div>`;
  const step = (k) => {
    if (k === 0) d.innerHTML = `<div class="ob">${dots(0)}<div class="eyebrow">Шаг 1 из 3 · Бизнес-радар</div><h2>Мы изучили вашу компанию</h2>
      <p class="muted">По продуктам банка, отрасли, ВЭД и графу платежей — пять факторов, через которые сервис отбирает и объясняет новости.</p>
      <ol class="ob-list">${top.map((e) => `<li><b>${esc(e.title)}</b><div class="muted small">${esc((e.reasons.find((r) => r.basis !== "baseline") || e.reasons[0] || {}).why || "")}</div></li>`).join("")}</ol>
      <div class="row"><button class="btn ghost" data-ob="skip" id="obSkip">Пропустить</button><button class="btn btn-red" data-ob="1">Дальше</button></div></div>`;
    if (k === 1) d.innerHTML = `<div class="ob">${dots(1)}<div class="eyebrow">Шаг 2 из 3</div><h2>Кто вы в компании?</h2>
      <p class="muted">От роли зависит, что в разборе выше: казначею — ставки и курс, бухгалтеру — налоги и нормы, собственнику — деньги и риски.</p>
      <div class="ob-roles">${Object.entries(state.roles).map(([r, t]) => `<button class="ob-role ${r === state.role ? "on" : ""}" data-role="${esc(r)}">${esc(t)}</button>`).join("")}
        <button class="ob-role ${state.role ? "" : "on"}" data-role="">Без роли</button></div>
      <div class="row"><button class="btn ghost" data-ob="0">Назад</button><button class="btn btn-red" data-ob="2">Дальше</button></div></div>`;
    if (k === 2) d.innerHTML = `<div class="ob">${dots(2)}<div class="eyebrow">Шаг 3 из 3</div><h2>Ваши цифры — точнее суммы</h2>
      <p class="muted">Без них «Деньги на кону» и сценарии считаются по оценкам из профиля. Если знаете долг, долю плавающей ставки, ФОТ или импорт — введите, это минута. Хранятся только в этом браузере.</p>
      <div class="row"><button class="btn ghost" data-ob="1">Назад</button><button class="btn ghost" data-ob="nums">Ввести цифры</button><button class="btn btn-red" data-ob="tour">Пройти тур (2 мин)</button><button class="btn" data-ob="skip">Начать</button></div></div>`;
    d.querySelectorAll("[data-role]").forEach((b) => b.onclick = () => {
      d.querySelectorAll("[data-role]").forEach((x) => x.classList.toggle("on", x === b));
      const r = b.dataset.role || "";
      if ((state.role || "") !== r) { $("#roleSelect").value = r; state.role = r || null; ctxLabel(); reload(); }
    });
    d.querySelectorAll("[data-ob]").forEach((b) => b.onclick = () => {
      const v = b.dataset.ob;
      if (v === "skip") return closeOnboarding();
      if (v === "tour") return startTour();
      if (v === "nums") { closeOnboarding(); return openNumbers(); }
      track("onboard_step", { meta: { step: +v } }); step(+v);
    });
  };
  step(0);
  d.showModal();
}
