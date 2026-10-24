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
