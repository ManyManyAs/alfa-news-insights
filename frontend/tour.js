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
  { view: "radar", sel: "#insightPanel .scenarios", open: true, title: "Сценарии и «что если»",
    text: "Три сценария на 12 месяцев в рублях: база — прогноз Альфа-Банка. Ниже калькулятор: двигайте ставку и курс, вводите свой долг — пересчёт мгновенный." },
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
        <span><button class="btn ghost small" data-t="prev">Назад</button> <button class="btn small" data-t="next">Далее</button></span></div></div>`;
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
    window.addEventListener("resize", () => tourStep >= 0 && place());
  }
  return o;
}

async function goStep(i) {
  if (i >= TOUR.length) return endTour();
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
  if (s.open) {
    if (!document.querySelector("#insightPanel .tabs") && state.feed.length) openInsight(state.feed[0].event_id);
    else if (!document.querySelector(s.sel) && state.insight) renderInsight("analysis");  // открыта другая вкладка разбора
  }
  const el = await waitFor(s.sel);
  if (tourStep !== i) return;
  o.querySelector("p").textContent = el ? s.text : s.text + " (элемент ещё загружается — откройте событие в ленте)";
  if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  setTimeout(place, 350);
}

function place() {
  const s = TOUR[tourStep], o = document.getElementById("tour");
  const el = s && document.querySelector(s.sel);
  const spot = o.querySelector(".tour-spot"), pop = o.querySelector(".tour-pop");
  if (!el) { spot.style.display = "none"; pop.style.cssText = "left:50%;top:40%;transform:translate(-50%,-50%)"; return; }
  const r = el.getBoundingClientRect(), pad = 8;
  spot.style.display = "block";
  Object.assign(spot.style, { left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px", height: Math.min(r.height, innerHeight - 40) + 2 * pad + "px" });
  const pw = Math.min(360, innerWidth - 32);
  let left = r.right + 16, top = r.top;
  if (left + pw > innerWidth - 16) left = Math.max(16, Math.min(r.left, innerWidth - pw - 16));
  if (left < r.right && left + pw > r.left) top = r.bottom + 16 > innerHeight - 220 ? Math.max(16, r.top - 230) : r.bottom + 16;
  pop.style.cssText = `left:${left}px;top:${Math.max(16, Math.min(top, innerHeight - 240))}px;width:${pw}px`;
}

function startTour() { closeOnboarding(); goStep(0); }
function endTour() { tourStep = -1; const o = document.getElementById("tour"); if (o) o.classList.remove("on"); }

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
    <div class="row"><button class="btn ghost" id="obSkip">Смотреть самостоятельно</button><button class="btn" id="obTour">Пройти тур (2 минуты)</button></div></div>`;
  d.showModal();
  document.getElementById("obSkip").onclick = closeOnboarding;
  document.getElementById("obTour").onclick = startTour;
}
function closeOnboarding() {
  try { localStorage.setItem("onboarded", "1"); } catch (e) { /* хранилище недоступно */ }
  const d = document.getElementById("onboard");
  if (d && d.open) d.close();
}
