// Этап 9: дизайн, доступность, телефон — клавиатура в ленте, свайпы карточек, высокая контрастность,
// установка как приложение и последняя лента без сети (service worker), фирменный PDF.

// ---------------------------------------------------------------- 84. клавиатура в ленте: j / k — вниз и вверх, Enter — открыть, x — скрыть
function feedCards() { return [...document.querySelectorAll("#feedList .feed-item")]; }
function focusCard(k) {
  const cards = feedCards();
  if (!cards.length) return;
  const i = Math.max(0, Math.min(cards.length - 1, k));
  cards.forEach((c, n) => c.classList.toggle("kb-focus", n === i));
  cards[i].focus({ preventScroll: true });
  cards[i].scrollIntoView({ block: "nearest", behavior: "smooth" });
  state.kbIdx = i;
}
function bindFeedKeys() {
  document.addEventListener("keydown", (e) => {
    if (document.body.dataset.view !== "radar" || document.querySelector("dialog[open]") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    const cards = feedCards(), cur = cards.indexOf(document.activeElement.closest ? document.activeElement.closest(".feed-item") : null);
    const at = cur >= 0 ? cur : state.kbIdx ?? -1;
    if (e.key === "j" || e.key === "о") { e.preventDefault(); focusCard(at + 1); }
    else if (e.key === "k" || e.key === "л") { e.preventDefault(); focusCard(at - 1); }
    else if ((e.key === "x" || e.key === "ч") && cards[at]) { e.preventDefault(); const id = cards[at].dataset.ev; hideEvent(id).then(() => focusCard(at)); }
    else if (e.key === "?" ) { e.preventDefault(); toast("Лента с клавиатуры: j / k — вниз и вверх, Enter — открыть, x — скрыть, / или Ctrl+K — поиск"); }
  });
}

// ---------------------------------------------------------------- 87. свайпы на телефоне: вправо — прочитано, влево — скрыть
function bindSwipes(root) {
  if (!matchMedia("(pointer: coarse)").matches) return;
  root.querySelectorAll(".feed-item").forEach((el) => {
    if (el.dataset.swipe) return;
    el.dataset.swipe = "1";
    let x0 = 0, y0 = 0, dx = 0, on = false;
    el.addEventListener("touchstart", (e) => { const t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; dx = 0; on = true; }, { passive: true });
    el.addEventListener("touchmove", (e) => {
      if (!on) return;
      const t = e.touches[0]; dx = t.clientX - x0;
      if (Math.abs(t.clientY - y0) > Math.abs(dx)) { on = false; el.style.transform = ""; return; }  // прокрутка, не свайп
      el.style.transform = `translateX(${dx * 0.6}px)`;
      el.classList.toggle("sw-left", dx < -40); el.classList.toggle("sw-right", dx > 40);
    }, { passive: true });
    el.addEventListener("touchend", () => {
      if (!on) return; on = false;
      el.style.transform = ""; el.classList.remove("sw-left", "sw-right");
      if (dx < -90) { hideEvent(el.dataset.ev); track("swipe", { event_id: el.dataset.ev, meta: { dir: "hide" } }); }
      else if (dx > 90) { markRead(el.dataset.ev); renderFeedList(); updateBadge(); toast("Отмечено прочитанным"); track("swipe", { event_id: el.dataset.ev, meta: { dir: "read" } }); }
    });
  });
}

// ---------------------------------------------------------------- 88. высокая контрастность
function applyContrast(on) { document.documentElement.dataset.contrast = on ? "more" : ""; }
function toggleContrast() {
  const on = document.documentElement.dataset.contrast !== "more";
  applyContrast(on);
  try { localStorage.setItem("bn:contrast", on ? "1" : "0"); } catch (e) { /* без хранилища — до перезагрузки */ }
  toast(on ? "Высокая контрастность включена" : "Обычная контрастность");
}
(function initContrast() {
  let saved = null;
  try { saved = localStorage.getItem("bn:contrast"); } catch (e) { saved = null; }
  applyContrast(saved === "1" || (saved == null && matchMedia("(prefers-contrast: more)").matches));
})();

// ---------------------------------------------------------------- 86. установка как приложение и лента без сети
function registerSW() {
  if (!("serviceWorker" in navigator) || location.protocol !== "https:" && location.hostname !== "localhost") return;
  addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* без офлайна — обычный сайт */ }));
}
// выход: последние данные компании из кэша удаляются — на чужом компьютере их не останется
async function clearOfflineCache() {
  try { if (window.caches) await caches.delete("br-api"); } catch (e) { /* нет кэша — нечего удалять */ }
}
let installEvt = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; const b = document.getElementById("installBtn"); if (b) b.hidden = false; });
function bindInstall() {
  const b = document.getElementById("installBtn");
  if (!b) return;
  b.onclick = async () => { if (!installEvt) return; installEvt.prompt(); const r = await installEvt.userChoice; if (r.outcome === "accepted") b.hidden = true; installEvt = null; track("pwa_install"); };
}
registerSW();

// ---------------------------------------------------------------- деньги при наведении на график накопления эффекта
const EC_LABEL = { negative: "Негативный", base: "Базовый", positive: "Позитивный" };
const EC_COL = { negative: "var(--risk)", base: "var(--watch)", positive: "var(--opp)" };
function ecHide(svg) {
  const box = svg.closest(".effect-chart"), tip = box && box.querySelector(".ec-tip");
  svg.querySelectorAll(".ec-guide, .ec-dot").forEach((el) => el.setAttribute("visibility", "hidden"));
  if (tip) tip.hidden = true;
}
function ecMove(svg, clientX) {
  let d = svg._ec;
  if (!d) { try { d = svg._ec = JSON.parse(svg.dataset.ec); } catch (e) { return; } }
  const r = svg.getBoundingClientRect(), px = (clientX - r.left) / r.width * d.W, step = (d.W - d.L - d.R) / 12;
  const k = Math.max(0, Math.min(11, Math.round((px - d.L) / step - 1)));
  const x = d.L + (k + 1) * step, y = (v) => d.T + (d.hi - v) * (d.H - d.T - d.B) / (d.hi - d.lo);
  const g = svg.querySelector(".ec-guide");
  g.setAttribute("x1", x); g.setAttribute("x2", x); g.setAttribute("visibility", "visible");
  d.s.forEach((s) => { const c = svg.querySelector(`.ec-dot[data-s="${s.n}"]`); if (c) { c.setAttribute("cx", x); c.setAttribute("cy", y(s.v[k])); c.setAttribute("visibility", "visible"); } });
  const box = svg.closest(".effect-chart"), tip = box.querySelector(".ec-tip");
  tip.innerHTML = `<b>${esc(d.months[k] || "")}</b><span class="muted small">накоплено с начала</span>` + ["negative", "base", "positive"].map((n) => d.s.find((s) => s.n === n)).filter(Boolean)
    .map((s) => `<div class="ec-tr"><i style="background:${EC_COL[s.n]}"></i><span>${EC_LABEL[s.n]}</span><b style="color:${EC_COL[s.n]}">${s.n === "base" && !s.v[k] ? "0 ₽" : rub(s.v[k])}</b></div>`).join("");
  tip.hidden = false;
  const br = box.getBoundingClientRect(), sx = r.left - br.left + x / d.W * r.width, w = tip.offsetWidth;
  let left = sx + 14;
  if (left + w > br.width - 4) left = sx - w - 14;
  tip.style.left = Math.max(4, left) + "px";
  tip.style.top = (r.top - br.top + 10) + "px";
}
document.addEventListener("pointermove", (e) => {
  const svg = e.target.closest && e.target.closest(".effect-chart svg[data-ec]");
  if (svg) { ecMove(svg, e.clientX); state.ecSvg = svg; } else if (state.ecSvg) { ecHide(state.ecSvg); state.ecSvg = null; }
});

// ---------------------------------------------------------------- горизонтальные полосы фильтров: колесо мыши и перетаскивание
function bindHScroll(el) {
  if (!el || el.dataset.hs) return;
  el.dataset.hs = "1";
  const edge = () => { el.classList.toggle("ov-l", el.scrollLeft > 2); el.classList.toggle("ov-r", el.scrollLeft + el.clientWidth < el.scrollWidth - 2); };
  el.addEventListener("wheel", (e) => {
    if (el.scrollWidth <= el.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
    e.preventDefault(); el.scrollLeft += e.deltaY;
  }, { passive: false });
  let x0 = null, s0 = 0, moved = false;
  el.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse" || el.scrollWidth <= el.clientWidth) return; x0 = e.clientX; s0 = el.scrollLeft; moved = false; });
  addEventListener("pointermove", (e) => { if (x0 == null) return; const dx = e.clientX - x0; if (Math.abs(dx) > 4) { moved = true; el.scrollLeft = s0 - dx; el.classList.add("dragging"); } });
  addEventListener("pointerup", () => { x0 = null; setTimeout(() => el.classList.remove("dragging"), 0); });
  el.addEventListener("click", (e) => { if (moved) { e.stopPropagation(); e.preventDefault(); moved = false; } }, true);
  el.addEventListener("scroll", edge, { passive: true });
  new ResizeObserver(edge).observe(el);
  edge();
}

// ---------------------------------------------------------------- клавиатура: Space нажимает элементы-кнопки, как Enter
document.addEventListener("keydown", (e) => {
  if (e.key !== " " || !e.target.matches || !e.target.matches('[role="button"]:not(button):not(input):not(textarea)')) return;
  e.preventDefault(); e.target.click();
});
