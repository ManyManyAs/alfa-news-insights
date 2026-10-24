// Календарь компании: два месяца рядом с перелистыванием. Дни — скруглённые квадратики с числом и кратким событием,
// при наведении — карточка-превью (как всплывающие подсказки в Википедии). Список событий по датам остаётся ниже.

const CAL_SHORT = { cbr: "ЦБ: ставка", norm: "Норма", task: "Действие" };
const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function monthGrid(year, month, byDay, todayStr) {
  const first = new Date(year, month, 1), last = new Date(year, month + 1, 0);
  const lead = (first.getDay() + 6) % 7;  // понедельник — первый
  const title = first.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  let cells = "";
  for (let i = 0; i < lead; i++) cells += `<span class="cg-d empty" aria-hidden="true"></span>`;
  for (let d = 1; d <= last.getDate(); d++) {
    const key = ymd(new Date(year, month, d)), evs = byDay[key] || [], wd = (lead + d - 1) % 7;
    const cls = [evs.length ? "has " + evs[0].kind : "", key === todayStr ? "today" : "", key < todayStr ? "past" : "", wd > 4 ? "we" : ""].join(" ");
    cells += `<button class="cg-d ${cls}" ${evs.length ? `data-cgday="${key}"` : "tabindex=\"-1\""} aria-label="${d} ${esc(title)}${evs.length ? ": " + esc(evs.map((e) => e.title).join("; ")) : ""}">
      <b>${d}</b>${evs.length ? `<span class="cg-t">${esc(CAL_SHORT[evs[0].kind] || "")}</span>${evs.length > 1 ? `<i class="cg-n">+${evs.length - 1}</i>` : ""}` : ""}</button>`;
  }
  return `<div class="cg-month"><div class="cg-title">${esc(title[0].toUpperCase() + title.slice(1))}</div>
    <div class="cg-wd">${WD.map((w, i) => `<span class="${i > 4 ? "we" : ""}">${w}</span>`).join("")}</div><div class="cg-days">${cells}</div></div>`;
}

function renderCalGrids(box, items, todayStr) {
  const byDay = {};
  items.forEach((x) => (byDay[x.date] = byDay[x.date] || []).push(x));
  const t = new Date(todayStr);
  let off = 0;
  const draw = () => {
    const a = new Date(t.getFullYear(), t.getMonth() + off, 1), b = new Date(t.getFullYear(), t.getMonth() + off + 1, 1);
    box.innerHTML = `<div class="cg-nav"><button class="icon-btn sm" data-cgp="-1" aria-label="Предыдущие месяцы" ${off <= 0 ? "disabled" : ""}>‹</button>
        <div class="cg-legend small"><span class="cbr">заседание ЦБ</span><span class="norm">вступает в силу</span><span class="task">моё действие</span></div>
        <button class="icon-btn sm" data-cgp="1" aria-label="Следующие месяцы" ${off >= 2 ? "disabled" : ""}>›</button></div>
      <div class="cg-wrap">${monthGrid(a.getFullYear(), a.getMonth(), byDay, todayStr)}${monthGrid(b.getFullYear(), b.getMonth(), byDay, todayStr)}</div>
      <div class="cg-pop" role="tooltip" hidden></div>`;
    box.querySelectorAll("[data-cgp]").forEach((btn) => btn.onclick = () => { off = Math.max(0, Math.min(2, off + +btn.dataset.cgp)); draw(); });
    const pop = box.querySelector(".cg-pop");
    let hideT = 0;
    const showPop = (el) => {
      clearTimeout(hideT);
      const evs = byDay[el.dataset.cgday] || [], d = new Date(el.dataset.cgday), n = Math.round((d - t) / 864e5);
      pop.innerHTML = evs.map((x) => { const [label, cls] = CAL_KIND[x.kind];
        return `<div class="cp-item ${cls}"><div class="cp-band"></div><div class="cp-body">
          <div class="cp-top"><span class="pill ${cls}">${label}</span><span class="muted small">${d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })} · ${n === 0 ? "сегодня" : n > 0 ? `через ${n} ${plural(n, "день", "дня", "дней")}` : `${-n} ${plural(-n, "день", "дня", "дней")} назад`}</span></div>
          <b>${esc(x.title)}</b>${x.note ? `<p>${esc(x.note)}</p>` : ""}
          ${x.event_id ? `<span class="cp-cta">Нажмите — открыть разбор →</span>` : x.task ? `<span class="cp-cta">Нажмите — к «Моим действиям» →</span>` : ""}</div></div>`; }).join("");
      pop.hidden = false;
      const r = el.getBoundingClientRect(), br = box.getBoundingClientRect(), pw = Math.min(340, br.width - 16);
      pop.style.width = pw + "px";
      let left = r.left - br.left + r.width / 2 - pw / 2;
      left = Math.max(8, Math.min(left, br.width - pw - 8));
      pop.style.left = left + "px";
      const below = r.bottom - br.top + 8;
      pop.style.top = below + "px";
      pop.classList.remove("up");
      requestAnimationFrame(() => { if (r.bottom + pop.offsetHeight + 16 > innerHeight) { pop.style.top = (r.top - br.top - pop.offsetHeight - 8) + "px"; pop.classList.add("up"); } });
    };
    const hidePop = () => { hideT = setTimeout(() => { pop.hidden = true; }, 120); };
    pop.onmouseenter = () => clearTimeout(hideT); pop.onmouseleave = hidePop;
    box.querySelectorAll("[data-cgday]").forEach((el) => {
      el.onmouseenter = () => showPop(el); el.onfocus = () => showPop(el);
      el.onmouseleave = hidePop; el.onblur = hidePop;
      el.onclick = () => {
        const evs = byDay[el.dataset.cgday] || [], x = evs.find((e) => e.event_id) || evs[0];
        if (!x) return;
        if (matchMedia("(pointer: coarse)").matches && pop.hidden) return showPop(el);  // на телефоне первое касание — превью
        if (x.event_id) { show("radar"); openInsight(x.event_id, false, "calendar"); }
        else if (x.task) show("tasks");
        else { const li = document.querySelector(`.cal-item[data-cdate="${el.dataset.cgday}"]`); if (li) { li.scrollIntoView({ behavior: "smooth", block: "center" }); li.classList.add("flash"); setTimeout(() => li.classList.remove("flash"), 1400); } }
      };
    });
  };
  draw();
}
