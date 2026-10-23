// Онбординг («мы изучили вашу компанию») и тур для жюри: подсветка элементов по шагам сценария.
/* global state, show, openInsight, renderInsight, esc */

const TOUR = [
  { view: "home", sel: "#radarWidget", title: "Точка входа на главной",
    text: "Виджет «Бизнес-радар» всегда на главной интернет-банка: 3–4 события, которые важны именно этой компании, с причиной «почему вам»." },
  { view: "home", sel: ".session", title: "Кто смотрит",
    text: "Смените пользователя, компанию или роль (финдиректор, казначей, главбух). Лента, причины и выводы перестроятся под профиль." },
  { view: "home", sel: "#radarCard", title: "Что давит на бизнес",
    text: "Контур — чувствительность компании к факторам по её продуктам, ВЭД, отрасли и графу платежей. Точки — сигналы из ленты: красные риски, зелёные возможности." },
  { view: "home", sel: "#profileCard", title: "Профиль и настройка",
    text: "Профиль, на котором строится персонализация: продукты банка, цепочка поставок, похожие компании. Нажмите на фактор — тема станет важнее или скроется." },
  { view: "radar", sel: ".feed-col", title: "Персональная лента",
    text: "Фильтры, поиск, сюжеты вместо дублей, доверие к источнику, «вступает в силу через N дней». Модель уточняет порядок и пишет причину у карточки." },
  { view: "radar", sel: "#insightPanel .impact-wrap", open: true, title: "Карта влияния",
    text: "Как событие доходит до компании: событие → фактор → признак компании или контрагент → эффект. Наведите на узел, чтобы увидеть путь. Это не пересказ новости." },
  { view: "radar", sel: "#insightPanel .effect-block", open: true, title: "Деньги: эффект за 12 месяцев",
    text: "Что событие может дать или стоить компании в рублях. База — прогноз Альфа-Банка, то есть «как в плане»; негативный и позитивный — умеренные отклонения. Если заметного влияния нет, сервис честно так и пишет. Ниже калькулятор «Что если» — подставьте свои цифры." },
  { view: "radar", sel: "#insightPanel .tabs", open: true, title: "Источники и трассировка",
    text: "У каждого утверждения — тип (факт / позиция банка / вывод сервиса) и ссылки. Вкладка «Как получен вывод» показывает шаги агента: модели, инструменты, стоимость." },
  { view: "digest", sel: "#digestBox", title: "Дайджест под роль",
    text: "Сводка за день или неделю для выбранной роли: главное, сроки, сюжеты, пересмотры прогнозов банка — и предпросмотр письма." },
];

let tourStep = -1;

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
        <span><button class="btn ghost small" data-t="prev">Назад</button> <button class="btn btn-red small" data-t="next">Далее</button></span></div></div>`;
    document.body.appendChild(o);
    o.querySelector("[data-t=end]").onclick = endTour;
    o.querySelector("[data-t=prev]").onclick = () => goStep(tourStep - 1);
    o.querySelector("[data-t=next]").onclick = () => goStep(tourStep + 1);
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
  tourStep = -1; const o = document.getElementById("tour"); if (o) o.classList.remove("on");
  const top = document.querySelector(".topbar"); if (top) top.classList.remove("ctx-open");
}

// Онбординг: при первом визите — «мы изучили вашу компанию: 5 факторов» и предложение пройти тур
function maybeOnboard() {
  let seen = false;
  try { seen = localStorage.getItem("onboarded") === "1"; } catch (e) { seen = false; }
  if (seen || !state.exposures) return;
  const top = state.exposures.filter((e) => e.weight > 0).slice(0, 5);
  const d = document.getElementById("onboard");
  d.innerHTML = `<div class="ob"><div class="eyebrow">Бизнес-радар</div>
    <h2>Мы изучили вашу компанию</h2>
    <p class="muted">По продуктам банка, отрасли, ВЭД и графу платежей — вот 5 факторов, которые для вас важнее всего. Новости будем отбирать и объяснять через них.</p>
    <ol class="ob-list">${top.map((e) => `<li><b>${esc(e.title)}</b><div class="muted small">${esc((e.reasons.find((r) => r.basis !== "baseline") || e.reasons[0] || {}).why || "")}</div></li>`).join("")}</ol>
    <div class="row"><button class="btn ghost" id="obSkip">Смотреть самостоятельно</button><button class="btn btn-red" id="obTour">Пройти тур (2 минуты)</button></div></div>`;
  d.showModal();
  document.getElementById("obSkip").onclick = closeOnboarding;
  document.getElementById("obTour").onclick = startTour;
}
function closeOnboarding() {
  try { localStorage.setItem("onboarded", "1"); } catch (e) { /* хранилище недоступно */ }
  const d = document.getElementById("onboard");
  if (d && d.open) d.close();
}
