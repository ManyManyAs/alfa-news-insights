// Этап 7: действия и связь с банком — заявка с суммой и причиной из расчёта, предпросмотр письма менеджеру,
// статусы обращений, канбан «Мои действия», деньги на кону у действия, сроки в колокольчике, все сроки в календарь,
// отчёт для руководителя, история по событию, «поручить» роли.

// суммы «Денег на кону» простым текстом (для письма менеджеру)
function moneyPlain(m) { return [m.down ? rub(m.down) : "", m.up ? rub(m.up) : ""].filter(Boolean).join(" … ") + " за год"; }

// ---------------------------------------------------------------- 61, 67. обращение в банк: контекст и предпросмотр
function actContext() {
  const ins = state.current && state.insight && state.insight.event_id === state.current ? state.insight : null;
  const sc = ins ? Object.fromEntries((ins.scenarios || []).map((s) => [s.name, s])) : {};
  const neg = sc.negative && !sc.negative.negligible ? sc.negative : null, pos = sc.positive && !sc.positive.negligible ? sc.positive : null;
  const m = ins ? eventMoney(ins.event_id) : null;
  return { ins, neg, pos, m };
}
function companyName() { const o = $("#companySelect").selectedOptions[0]; return o ? (o.dataset.name || o.textContent) : ""; }
function draftComment(kind, product, c) {
  if (!c.ins) return "";
  const lines = [`По событию: «${c.ins.headline}».`];
  if (c.neg) lines.push(`По расчёту сервиса негативный сценарий — ${rub(c.neg.total_rub)} за 12 месяцев${myNums() ? " (по нашим цифрам)" : " (оценка по профилю)"}.`);
  else if (c.m) lines.push(`По «Деньгам на кону»: ${moneyPlain(c.m)}.`);
  if (kind === "application" && product) lines.push(`Хотим обсудить: ${product}${c.neg ? ` — чтобы ограничить риск примерно на ${rub(Math.abs(c.neg.total_rub)).replace(/^[−+]/, "")}` : ""}.`);
  else if (kind === "manager") lines.push("Хотим обсудить, что из продуктов банка поможет.");
  return lines.join(" ");
}
function openAction(kind, product) {
  track("action_open", { event_id: state.current, meta: { kind } });
  const d = $("#actionDialog"), c = actContext();
  const role = state.role ? state.roles[state.role] : "";
  d.innerHTML = `<form method="dialog" class="act-form act-v2"><h3>${esc(ACT_TITLE[kind])}${product ? ": " + esc(product) : ""}</h3>
    <label class="small muted" for="actComment">Сообщение менеджеру — заполнено по разбору, поправьте как удобно</label>
    <textarea id="actComment" rows="5" maxlength="1000" placeholder="Удобное время, вопрос, сумма…">${esc(draftComment(kind, product, c))}</textarea>
    <div class="act-prev"><div class="sub-h">Что увидит менеджер</div><dl>
      <dt>Компания</dt><dd>${esc(companyName())}${role ? ` · ${esc(role)}` : ""}</dd>
      <dt>Обращение</dt><dd>${esc(ACT_TITLE[kind])}${product ? ` · ${esc(product)}` : ""}</dd>
      ${c.ins ? `<dt>Событие</dt><dd>${esc(c.ins.headline)}</dd>` : ""}
      ${c.neg || c.pos ? `<dt>Расчёт</dt><dd>${c.neg ? `<span class="bad">${rub(c.neg.total_rub)}</span> негативный` : ""}${c.neg && c.pos ? " · " : ""}${c.pos ? `<span class="ok">${rub(c.pos.total_rub)}</span> позитивный` : ""} за 12 мес., калькулятор</dd>` : ""}
      <dt>Сообщение</dt><dd id="actPrevText" class="act-prev-t"></dd></dl>
      <p class="muted small">${actWhere()}</p></div>
    <div class="row"><button value="cancel" class="btn ghost">Отмена</button><button class="btn" id="actSend" value="send">Отправить</button></div></form>`;
  const sync = () => { $("#actPrevText").textContent = $("#actComment").value.trim() || "без комментария"; };
  $("#actComment").oninput = sync; sync();
  d.showModal();
  $("#actSend").onclick = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/actions", { method: "POST", body: JSON.stringify({ company_id: state.company, event_id: state.current, kind, product: product || null, comment: $("#actComment").value.trim() || null }) });
      d.close(); toast(`${r.title}: запрос ${r.ticket} принят. Статус — в «Моих действиях».`);
      state.requests = null;
      if (document.body.dataset.view === "tasks") loadTasks();
      const h = document.getElementById("evHistory"); if (h) renderEventHistory();
    } catch (err) { toast("Не удалось отправить: " + err.message); }
  };
}

// ---------------------------------------------------------------- 62. мои обращения в банк
async function loadRequests() {
  try { state.requests = await api(`/api/actions?${q({ company_id: state.company })}`); } catch (e) { state.requests = { items: [], statuses: {} }; }
  return state.requests;
}
const REQ_CLASS = { accepted: "watch", contacted: "opportunity", resolved: "opportunity", cancelled: "muted" };
function requestsCard(r) {
  if (!r.items.length) return "";
  const when = (s) => s ? new Date(s.replace(" ", "T") + "Z").toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "";
  return `<div class="card req-card"><div class="mini-head"><h3>Мои обращения в банк</h3><span class="muted small">${r.items.length}</span></div>
    <ul class="req-list">${r.items.slice(0, 8).map((x) => `<li class="req ${x.status}">
      <div class="req-main"><b>${esc(x.title)}${x.product ? ` · ${esc(x.product)}` : ""}</b>
        <span class="muted small">${esc(x.ticket)} · ${when(x.created_at)}${x.event_title ? ` · ${esc(x.event_title.length > 70 ? x.event_title.slice(0, 70) + "…" : x.event_title)}` : ""}</span></div>
      <span class="pill ${REQ_CLASS[x.status] || "watch"}">${esc(r.statuses[x.status] || x.status)}</span>
      ${x.status === "accepted" ? `<span class="req-btns"><button class="link-btn" data-req="${x.id}:resolved">решено</button><button class="link-btn" data-req="${x.id}:cancelled">отозвать</button></span>` : ""}</li>`).join("")}</ul>
    <p class="muted small">В банке статус «менеджер связался» приходит из CRM; в демо обращение сохраняется со статусом «принято».</p></div>`;
}

// ---------------------------------------------------------------- 63, 64, 70. канбан «Мои действия»
const COLS = [["open", "Сделать"], ["doing", "В работе"], ["done", "Готово"]];
function ownerOptions(cur) {
  const roles = Object.values(state.roles || {});
  const list = [...new Set([...(cur && !roles.includes(cur) ? [cur] : []), ...roles])];
  return `<option value="">не назначен</option>` + list.map((t) => `<option ${t === cur ? "selected" : ""}>${esc(t)}</option>`).join("");
}
function taskCard(t, today) {
  const d = /\d{1,2}\.\d{1,2}/.test(t.due || "") ? dueDate(t.due) : null;
  const late = t.status !== "done" && d && d < today, soon = t.status !== "done" && d && !late && (d - today) / 864e5 <= 3;
  const m = t.event_id ? eventMoney(t.event_id) : null;
  const ci = COLS.findIndex(([k]) => k === t.status);
  return `<li class="kb-card ${late ? "late" : ""} ${soon ? "soon" : ""}" draggable="true" data-tid="${t.id}">
    <b>${esc(t.text)}</b>
    ${m ? `<div class="kb-money" title="Сумма из «Денег на кону» по событию этого действия">${ico("money")} на кону по событию: ${moneyText(m)}</div>` : ""}
    <div class="kb-meta"><label class="kb-owner">${ico("user")}<select data-towner="${t.id}" aria-label="Исполнитель">${ownerOptions(t.owner)}</select></label>
      ${t.due ? `<span class="${late ? "bad" : soon ? "warn" : "muted"} small">${ico("clock")} ${esc(t.due)}${late ? " · срок прошёл" : soon ? " · скоро" : ""}</span>` : ""}</div>
    ${t.event_id ? `<button class="link-btn small" data-tev="${esc(t.event_id)}">По событию: ${esc(t.event_title ? (t.event_title.length > 60 ? t.event_title.slice(0, 60) + "…" : t.event_title) : "открыть разбор")}</button>` : ""}
    <div class="kb-btns">${ci > 0 ? `<button class="icon-btn sm" data-tmove="${t.id}:${COLS[ci - 1][0]}" aria-label="В «${COLS[ci - 1][1]}»" title="В «${COLS[ci - 1][1]}»">←</button>` : ""}
      ${ci < 2 ? `<button class="icon-btn sm" data-tmove="${t.id}:${COLS[ci + 1][0]}" aria-label="В «${COLS[ci + 1][1]}»" title="В «${COLS[ci + 1][1]}»">→</button>` : ""}
      <button class="icon-btn sm" data-tics="${t.id}" title="Напомнить в календаре" aria-label="Напомнить в календаре">${ico("cal")}</button>
      <button class="icon-btn sm" data-tdel="${t.id}" title="Удалить" aria-label="Удалить">×</button></div></li>`;
}
async function patchTask(id, body) {
  await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ company_id: state.company, ...body }) });
}
async function loadTasks() {
  const box = $("#tasksBox");
  if (!box.children.length || box.querySelector(".skeleton")) box.innerHTML = `<div class="skeleton"></div>`;
  const [, reqs] = await Promise.all([loadTaskCount(), loadRequests()]);
  const tasks = state.tasks || [], today = new Date(); today.setHours(0, 0, 0, 0);
  const tools = `<div class="kb-tools">${tasks.some((t) => t.status !== "done") ? `<button class="btn ghost small" id="icsAll">${ico("cal")} Все сроки в календарь</button>` : ""}
    <button class="btn ghost small" id="reportBtn">${ico("doc")} Отчёт для руководителя (PDF)</button></div>`;
  box.innerHTML = (tasks.length ? `${tools}<div class="kanban">${COLS.map(([k, title]) => { const list = tasks.filter((t) => t.status === k);
      return `<section class="kb-col" data-col="${k}"><div class="kb-head"><b>${title}</b><span class="kb-n">${list.length}</span></div>
        <ul class="kb-list">${list.map((t) => taskCard(t, today)).join("") || `<li class="kb-empty">${k === "done" ? "Здесь появится выполненное" : "Пусто"}</li>`}</ul></section>`; }).join("")}</div>`
    : `${tools}<div class="card e-card">${emptyArt("tasks")}<h3>Пока пусто</h3><p class="muted">Откройте разбор события и нажмите «＋ В мои действия» у шага, который берёте в работу: он появится здесь с исполнителем и сроком.</p>
      <button class="btn btn-red small" data-goto2="radar">Открыть ленту</button></div>`) + requestsCard(reqs);
  const reload = () => { loadTasks(); renderStrip(); };
  box.querySelectorAll("[data-tmove]").forEach((b) => b.onclick = async () => { const [id, st] = b.dataset.tmove.split(":"); await patchTask(id, { status: st }); reload(); });
  box.querySelectorAll("[data-towner]").forEach((s) => s.onchange = async () => { await patchTask(s.dataset.towner, { owner: s.value }); toast(s.value ? `Поручено: ${s.value}` : "Исполнитель снят"); loadTaskCount(); });
  box.querySelectorAll("[data-tdel]").forEach((b) => b.onclick = async () => { await api(`/api/tasks/${b.dataset.tdel}?${q({ company_id: state.company })}`, { method: "DELETE" }); reload(); });
  box.querySelectorAll("[data-tics]").forEach((b) => b.onclick = () => { const t = tasks.find((x) => x.id === +b.dataset.tics); downloadIcs(t, t.event_title || "Мои действия"); });
  box.querySelectorAll("[data-tev]").forEach((b) => b.onclick = () => { show("radar"); openInsight(b.dataset.tev, false, "tasks"); });
  box.querySelectorAll("[data-goto2]").forEach((b) => b.onclick = () => show(b.dataset.goto2));
  box.querySelectorAll("[data-req]").forEach((b) => b.onclick = async () => {
    const [id, st] = b.dataset.req.split(":");
    await api(`/api/actions/${id}`, { method: "PATCH", body: JSON.stringify({ company_id: state.company, status: st }) });
    toast(st === "resolved" ? "Обращение отмечено решённым" : "Обращение отозвано"); loadTasks();
  });
  const ia = document.getElementById("icsAll"); if (ia) ia.onclick = () => icsAll(tasks.filter((t) => t.status !== "done"));
  document.getElementById("reportBtn").onclick = printReport;
  // перетаскивание карточек между колонками (мышью; на телефоне — кнопки ← →)
  box.querySelectorAll(".kb-card").forEach((c) => c.ondragstart = (e) => { e.dataTransfer.setData("text/plain", c.dataset.tid); c.classList.add("drag"); });
  box.querySelectorAll(".kb-col").forEach((col) => {
    col.ondragover = (e) => { e.preventDefault(); col.classList.add("over"); };
    col.ondragleave = () => col.classList.remove("over");
    col.ondrop = async (e) => { e.preventDefault(); col.classList.remove("over"); const id = e.dataTransfer.getData("text/plain"); const t = tasks.find((x) => String(x.id) === id);
      if (t && t.status !== col.dataset.col) { await patchTask(id, { status: col.dataset.col }); reload(); } };
  });
}

// ---------------------------------------------------------------- 65. сроки скоро — в колокольчике
function tasksDue() {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return (state.tasks || []).filter((t) => t.status !== "done" && /\d{1,2}\.\d{1,2}/.test(t.due || "")).map((t) => ({ t, d: dueDate(t.due) }))
    .filter(({ d }) => (d - today) / 864e5 <= 3).map(({ t, d }) => ({ ...t, days: Math.round((d - today) / 864e5) })).sort((a, b) => a.days - b.days);
}
function tasksDueHtml() {
  const list = tasksDue();
  if (!list.length) return "";
  return `<div class="bell-sec">Сроки по моим действиям</div><ul>${list.slice(0, 5).map((t) => `<li data-goview="tasks" tabindex="0" role="button"><b>${esc(t.text.length > 90 ? t.text.slice(0, 90) + "…" : t.text)}</b>
    <span class="muted small">${t.days < 0 ? `срок прошёл ${-t.days} ${plural(-t.days, "день", "дня", "дней")} назад` : t.days === 0 ? "срок сегодня" : `через ${t.days} ${plural(t.days, "день", "дня", "дней")}`}${t.owner ? ` · ${esc(t.owner)}` : ""}</span></li>`).join("")}</ul>`;
}

// ---------------------------------------------------------------- 66. все сроки одним файлом календаря
function icsAll(list) {
  const ymd = (x) => `${x.getFullYear()}${String(x.getMonth() + 1).padStart(2, "0")}${String(x.getDate()).padStart(2, "0")}`;
  const clean = (t) => String(t || "").replace(/[\\;,]/g, (c) => "\\" + c).replace(/\n/g, " ");
  const ev = list.map((a, k) => { const d = dueDate(a.due), n = new Date(d); n.setDate(d.getDate() + 1);
    return ["BEGIN:VEVENT", `UID:${Date.now()}-${k}-${a.id}@business-radar`, `DTSTAMP:${ymd(new Date())}T090000Z`, `DTSTART;VALUE=DATE:${ymd(d)}`, `DTEND;VALUE=DATE:${ymd(n)}`,
      `SUMMARY:${clean(a.text.slice(0, 120))}`, `DESCRIPTION:${clean(`${a.owner ? "Исполнитель: " + a.owner + ". " : ""}${a.event_title ? "По событию: " + a.event_title + ". " : ""}Бизнес-радар, Альфа-Бизнес.`)}`,
      "BEGIN:VALARM", "TRIGGER:-PT9H", "ACTION:DISPLAY", "DESCRIPTION:Напоминание Бизнес-радара", "END:VALARM", "END:VEVENT"].join("\r\n"); });
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Alfa-Business//Business Radar//RU", ...ev, "END:VCALENDAR"].join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([ics], { type: "text/calendar" }));
  link.download = "moi-deystviya.ics"; document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  toast(`${list.length} ${plural(list.length, "срок", "срока", "сроков")} — откройте файл, чтобы добавить в календарь`);
}

// ---------------------------------------------------------------- 68. отчёт для руководителя: одна страница
function printReport() {
  const box = document.getElementById("reportPrint");
  const read = state.feed || [];
  const top = [...read].sort((a, b) => imp(b) - imp(a)).slice(0, 3);
  const m = state.money, open = (state.tasks || []).filter((t) => t.status !== "done");
  const reqs = ((state.requests || {}).items || []).filter((x) => x.status === "accepted");
  const role = state.role ? state.roles[state.role] : "";
  box.innerHTML = `<h1>Бизнес-радар: главное для руководителя</h1><p class="rp-meta">${esc(companyName())}${role ? ` · взгляд: ${esc(role)}` : ""}</p>
    <h2>Три главных события</h2><ol>${top.map((it) => { const mm = eventMoney(it.event_id);
      return `<li><b>${esc(it.title)}</b><br><span>${esc(EFFECT[it.effect])} · важность ${esc(IMP_RU[impLevel(it).level])}${mm ? ` · ${moneyText(mm)}` : ""}</span><br><span class="rp-why">${esc((it.reasons[0] || {}).why || "")}</span></li>`; }).join("")}</ol>
    <h2>Деньги на кону за 12 месяцев</h2>${m && (m.down || m.up) ? `<p>Возможный минус: <b>${rub(m.down || 0)}</b> · возможный плюс: <b>${rub(m.up || 0)}</b>. Суммы — калькулятор по формулам, не модель.</p>` : `<p>Заметного влияния на прибыль по текущим событиям нет.</p>`}
    <h2>Действия в работе · ${open.length}</h2>${open.length ? `<ul>${open.slice(0, 8).map((t) => `<li>${esc(t.text)}${t.owner ? ` — <i>${esc(t.owner)}</i>` : ""}${t.due ? `, ${esc(t.due)}` : ""}</li>`).join("")}</ul>` : "<p>Нет открытых действий.</p>"}
    ${reqs.length ? `<h2>Ждут ответа банка · ${reqs.length}</h2><ul>${reqs.map((x) => `<li>${esc(x.ticket)} · ${esc(x.title)}${x.product ? ` · ${esc(x.product)}` : ""}</li>`).join("")}</ul>` : ""}
    <p class="rp-disc">${esc(DISCLAIMER)}</p>`;
  printDoc("report");
}

// ---------------------------------------------------------------- 69. что уже сделано по событию
async function renderEventHistory() {
  const box = document.getElementById("evHistory");
  if (!box || !state.current) return;
  const id = state.current;
  if (!state.requests) await loadRequests();
  if (!state.tasks) await loadTaskCount();
  if (state.current !== id) return;
  const tasks = (state.tasks || []).filter((t) => t.event_id === id), reqs = ((state.requests || {}).items || []).filter((x) => x.event_id === id);
  const st = { open: "сделать", doing: "в работе", done: "готово" };
  box.innerHTML = tasks.length || reqs.length ? `<div class="sub-h">Что уже сделано по событию</div><ul class="evh">
    ${tasks.map((t) => `<li class="evh-${t.status}">${t.status === "done" ? "✓" : "○"} ${esc(t.text)} <span class="muted small">· ${st[t.status] || t.status}${t.owner ? `, ${esc(t.owner)}` : ""}</span></li>`).join("")}
    ${reqs.map((x) => `<li>✉ ${esc(x.title)}${x.product ? ` · ${esc(x.product)}` : ""} <span class="muted small">· ${esc(x.ticket)}, ${esc((state.requests.statuses || {})[x.status] || x.status)}</span></li>`).join("")}</ul>
    <button class="link-btn small" data-goview="tasks">Все действия →</button>` : "";
  box.querySelectorAll("[data-goview]").forEach((b) => b.onclick = () => show("tasks"));
}
