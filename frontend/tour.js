// Онбординг («мы изучили вашу компанию») и тур по сервису: подсветка элементов по шагам сценария.
/* global state, show, openInsight, renderInsight, esc */

const TOUR = [
  { view: "home", sel: "#homeStrip", title: "Точка входа на главной · критерий 09",
    text: "Сводка «Дела по радару»: новые риски, открытые действия, ближайшая дата и пересмотры прогноза банка. Нажмите на плитку — откроется нужный раздел." },
  { view: "home", sel: ".session", title: "Кто смотрит · критерий 01",
    text: "Смените компанию или роль (финдиректор, казначей, главбух). Лента, причины и выводы перестроятся под профиль." },
  { view: "home", sel: "#focusCard", title: "Главное сейчас · критерий 02",
    text: "Одно событие, которое стоит открыть первым, и цепочка влияния: событие → фактор → через что задевает компанию → деньги за год." },
  { view: "home", sel: "#cmdBtn", title: "Поиск по всему сервису",
    text: "Ctrl+K или «/» — события ленты, разделы, действия и ответ из аналитики банка в одном окне." },
  { view: "home", sel: "#moneyCard", title: "Деньги на кону",
    text: "Что события ленты могут стоить или дать за 12 месяцев — по причинам, без двойного счёта. Суммы считает калькулятор, а не модель." },
  { view: "radar", sel: ".feed-col", title: "Персональная лента · критерии 01, 04",
    text: "Фильтры со счётчиками, сюжеты вместо дублей, «вступает в силу через N дней», пометка «другой регион». У каждой карточки — короткая причина «почему вам»." },
  { view: "radar", sel: "#insightPanel .impact-wrap", open: true, title: "Карта влияния · критерии 02, 11",
    text: "Как событие доходит до компании: событие → фактор → признак компании или контрагент → эффект. Наведите на узел, чтобы увидеть путь." },
  { view: "radar", sel: "#insightPanel .effect-block", open: true, title: "Деньги: эффект за 12 месяцев",
    text: "База — прогноз Альфа-Банка («как в плане»); негативный и позитивный — умеренные отклонения. Ниже калькулятор «Что если» — подставьте свои цифры." },
  { view: "radar", sel: "#insightPanel .bv-card", open: true, title: "Позиция Альфа-Банка · критерий 03",
    text: "Тезисы и прогнозы из аналитики банка со ссылкой на документ и страницу. Позиция банка всегда отделена от выводов модели." },
  { view: "radar", sel: "#insightPanel .tabs", open: true, title: "Источники и трассировка · критерий 12",
    text: "У каждого утверждения — тип и ссылки. Вкладка «Как получен вывод» показывает шаги агента: модели, инструменты, время и стоимость." },
  { view: "radar", sel: "#insightPanel .chat", open: true, title: "Чат по разбору · критерий 10",
    text: "Подсказки под событие, ответ про деньги с мини-графиком из калькулятора, «объясни проще» и «для собственника», сравнение с другим событием." },
  { view: "forecasts", sel: "#forecastsBox", title: "Прогнозы банка",
    text: "Макропрогноз Альфа-Банка по темам компании с историей пересмотров. Новые пересмотры отмечены и попадают в уведомления." },
  { view: "digest", sel: "#digestBox", title: "Дайджест под роль",
    text: "Сводка за день или неделю: главное, сроки, сюжеты, пересмотры прогнозов. Рядом — подписка на уведомления в браузере. Можно сохранить в PDF." },
  { view: "tasks", sel: "#tasksBox", title: "Мои действия и обращения · критерий 06",
    text: "Доска «сделать — в работе — готово» с исполнителем и деньгами на кону; обращения в банк со статусом; все сроки — в календарь; отчёт для руководителя одной страницей." },
  { view: "home", sel: "#feedChat", title: "Вопрос по всей ленте · критерий 10",
    text: "«Что главное на неделе для казначея?» — ответ по всем событиям ленты со ссылками на разборы." },
  { view: "metrics", sel: "#opsBox", title: "Агент изнутри · критерии 08, 11, 12",
    text: "Бюджет моделей с прогнозом до сброса, отбитые атаки red-team, сравнение моделей и что ИИ отсеял для «Денег на кону». Схема шагов агента и правки критика — во вкладке «Как получен вывод»." },
];

let tourStep = -1, tourTimer = 0;
// «Сценарий для жюри»: тур идёт сам — шаг каждые 8 секунд, полоска показывает, сколько осталось до следующего
const TOUR_AUTO_MS = 8000;
function tourAuto(on) {
  clearInterval(tourTimer); tourTimer = 0;
  const o = document.getElementById("tour"), b = o && o.querySelector("[data-t=auto]");
  if (b) { b.setAttribute("aria-pressed", String(on)); b.textContent = on ? "❚❚ Пауза" : "▶ Сам"; }
  if (o) o.classList.toggle("auto", on);
  if (!on) return;
  if (typeof track === "function") track("tour_start", { meta: { auto: 1 } });
  tourTimer = setInterval(() => goStep(tourStep + 1), TOUR_AUTO_MS);
  restartProg();
}
function restartProg() {
  const i = document.querySelector("#tour .tour-prog i");
  if (!i) return;
  i.style.animation = "none"; void i.offsetWidth; i.style.animation = tourTimer ? `tourProg ${TOUR_AUTO_MS}ms linear` : "none";
}

function waitFor(sel, ms = 90000) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      const el = document.querySelector(sel);
      if (el && el.getBoundingClientRect().height > 0) return resolve(el);
      if (Date.now() - t0 > ms) return resolve(null);
      setTimeout(tick, 250);
    };
    tick();
  });
}

function tourUi() {
  let o = document.getElementById("tour");
  if (!o) {
    o = document.createElement("div");
    o.id = "tour";
    o.innerHTML = `<div class="tour-spot"></div><div class="tour-pop" role="dialog" aria-live="polite">
      <div class="tour-n"></div><h3></h3><p></p>
      <div class="row"><button class="btn ghost small" data-t="end">Закончить</button>
        <span><button class="btn ghost small" data-t="auto" aria-pressed="false" title="Тур пойдёт сам: шаг каждые 8 секунд">▶ Сам</button> <button class="btn ghost small" data-t="prev">Назад</button> <button class="btn btn-red small" data-t="next">Далее</button></span></div>
      <div class="tour-prog" aria-hidden="true"><i></i></div></div>`;
    document.body.appendChild(o);
    o.querySelector("[data-t=end]").onclick = endTour;
    o.querySelector("[data-t=prev]").onclick = () => goStep(tourStep - 1);
    o.querySelector("[data-t=next]").onclick = () => goStep(tourStep + 1);
    o.querySelector("[data-t=auto]").onclick = () => tourAuto(!tourTimer);
    document.addEventListener("keydown", (e) => {
      if (tourStep < 0) return;
      if (e.key === "Escape") endTour();
      if (e.key === "ArrowRight") goStep(tourStep + 1);
      if (e.key === "ArrowLeft") goStep(tourStep - 1);
    });
    window.addEventListener("resize", schedulePlace);
    document.addEventListener("scroll", schedulePlace, true);  // capture: ловим прокрутку и внутренних панелей
  }
  return o;
}

async function goStep(i) {
  if (i >= TOUR.length) { if (typeof track === "function") track("tour_finish", { value: TOUR.length }); return endTour(); }
  if (i < 0) return;
  tourStep = i;
  restartProg();
  const s = TOUR[i], o = tourUi();
  o.classList.add("on");
  o.querySelector(".tour-n").textContent = `Шаг ${i + 1} из ${TOUR.length}`;
  o.querySelector("h3").textContent = s.title;
  o.querySelector("p").textContent = "Загружаю…";
  o.querySelector("[data-t=next]").textContent = i === TOUR.length - 1 ? "Готово" : "Далее";
  o.querySelector(".tour-spot").style.display = "none";
  show(s.view);
  // одна колонка (телефон, планшет): шаги про разбор показывают экран разбора, шаги про ленту — ленту
  document.body.classList.toggle("m-detail", !!s.open && matchMedia("(max-width: 1100px)").matches);
  // телефон: выбор компании и роли спрятан в меню — для шага «Кто смотрит» раскрываем его
  const top = document.querySelector(".topbar");
  if (top) top.classList.toggle("ctx-open", s.sel === ".session" && matchMedia("(max-width: 640px)").matches);
  if (s.open) {
    if (!state.detail) { state.detail = true; if (state.insight) renderInsight("analysis"); }  // шаги про детали разбора
    if (!document.querySelector("#insightPanel .tabs") && state.feed.length) openInsight(state.feed[0].event_id);
    else if (!document.querySelector(s.sel) && state.insight) renderInsight("analysis");  // открыта другая вкладка разбора
  }
  const el = await waitFor(s.sel);
  if (tourStep !== i) return;
  o.querySelector("p").textContent = el ? s.text : s.text + " (элемент ещё загружается — откройте событие в ленте)";
  if (el) {
    // крупный блок — к началу, чтобы снизу осталось место для подсказки; небольшой — в центр
    const tall = el.getBoundingClientRect().height > innerHeight * 0.55;
    el.scrollIntoView({ block: tall ? "start" : "center", behavior: "smooth" });
  }
  place();
  setTimeout(place, 400);
  setTimeout(place, 900);
}

// Подсветка следует за элементом при любой прокрутке (страницы или внутренней панели) и не выходит за экран;
// подсказка ставится туда, где она не закрывает подсвеченный блок: справа, слева, снизу, сверху — или в угол.
let placeQueued = false;
function schedulePlace() {
  if (tourStep < 0 || placeQueued) return;
  placeQueued = true;
  requestAnimationFrame(() => { placeQueued = false; place(); });
}
function place() {
  const s = TOUR[tourStep], o = document.getElementById("tour");
  if (!s || !o) return;
  const el = document.querySelector(s.sel);
  const spot = o.querySelector(".tour-spot"), pop = o.querySelector(".tour-pop");
  const M = 12, pad = 6, W = innerWidth, H = innerHeight;
  const pw = Math.min(340, W - 2 * M), ph = pop.offsetHeight || 200;
  if (!el) {
    spot.style.display = "none";
    pop.style.cssText = `left:${(W - pw) / 2}px;top:${H * 0.3}px;width:${pw}px`;
    return;
  }
  const r = el.getBoundingClientRect();
  // видимая часть элемента (не заходя под липкую шапку, если подсвечиваем не саму шапку)
  const bar = document.querySelector(".topbar"), inBar = bar && bar.contains(el);
  const topMin = inBar ? M / 2 : Math.max(M / 2, (bar ? bar.getBoundingClientRect().bottom : 0) + 4);
  const v = { left: Math.max(r.left - pad, M / 2), top: Math.max(r.top - pad, topMin),
    right: Math.min(r.right + pad, W - M / 2), bottom: Math.min(r.bottom + pad, H - M / 2) };
  const visible = v.right > v.left && v.bottom > v.top;
  spot.style.display = visible ? "block" : "none";
  if (visible) Object.assign(spot.style, { left: v.left + "px", top: v.top + "px", width: v.right - v.left + "px", height: v.bottom - v.top + "px" });
  const fitsY = (t) => Math.min(Math.max(t, M), H - ph - M);
  const cands = [
    { left: v.right + M, top: fitsY(v.top), ok: v.right + M + pw <= W - M },
    { left: v.left - M - pw, top: fitsY(v.top), ok: v.left - M - pw >= M },
    { left: Math.min(Math.max(v.left, M), W - pw - M), top: v.bottom + M, ok: v.bottom + M + ph <= H - M },
    { left: Math.min(Math.max(v.left, M), W - pw - M), top: v.top - M - ph, ok: v.top - M - ph >= M },
  ];
  const c = cands.find((x) => x.ok) || { left: W - pw - M, top: H - ph - M };  // крупный блок — подсказка в угол
  pop.style.cssText = `left:${c.left}px;top:${c.top}px;width:${pw}px`;
  pop.classList.toggle("corner", !cands.some((x) => x.ok));
}

function startTour() { closeOnboarding(); if (typeof track === "function") track("tour_start"); goStep(0); }
function endTour() {
  tourAuto(false);
  tourStep = -1; const o = document.getElementById("tour"); if (o) o.classList.remove("on");
  const top = document.querySelector(".topbar"); if (top) top.classList.remove("ctx-open");
}

// Онбординг при первом визите: «мы изучили вашу компанию», выбор роли, «мои цифры» и тур
function maybeOnboard() {
  let seen = false;
  try { seen = localStorage.getItem("onboarded") === "1"; } catch (e) { seen = false; }
  if (seen || !state.exposures) return;
  onboardSteps();  // 3 шага: компания → роль → мои цифры (home.js)
}
function closeOnboarding() {
  try { localStorage.setItem("onboarded", "1"); } catch (e) { /* хранилище недоступно */ }
  const d = document.getElementById("onboard");
  if (d && d.open) d.close();
}
