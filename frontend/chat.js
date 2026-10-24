// Этап 6: чат по разбору и по всей ленте — подсказки под событие, суммы мини-графиком, «объясни проще» и «для собственника»,
// ссылки на источник с подсветкой, ответ → «мои действия», история по событию, оценка ответа, сравнение двух событий.
// Суммы в ответах — только из калькулятора (scenario.py), модель их не придумывает.

// ---------------------------------------------------------------- 51. подсказки вопросов под конкретное событие
function chatSuggestions(i) {
  const f = (state.feed || []).find((x) => x.event_id === state.current) || {};
  const out = [];
  const sc = (i.scenarios || []).find((s) => s.name === "negative");
  if (sc && !sc.negligible) out.push("Сколько это может стоить нам в рублях?");
  if (f.effective && f.effective.days >= 0) out.push(`Что нужно успеть до ${new Date(f.effective.date).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}?`);
  if ((i.bank_view || []).length) out.push("Что об этом думает Альфа-Банк?");
  else out.push("Есть ли у банка позиция по этой теме?");
  const p = (i.products || [])[0];
  if (p) out.push(`Чем поможет ${(PRODUCT_RU[p.product] || p.product).toLowerCase()}?`);
  const risk = (i.consequences && i.consequences.risks || [])[0];
  if (risk) out.push("Какой риск главный и как его снизить?");
  if (state.profile && (state.profile.counterparties || []).length) out.push("Как это затронет наших покупателей и поставщиков?");
  out.push("Что делать в первую очередь?", "Откуда эти выводы?");
  return [...new Set(out)].slice(0, 6);
}

// ---------------------------------------------------------------- 57. история чата по событию (в браузере)
const chatKey = () => `bn:chat:${state.company}:${state.role || "-"}:${state.current}`;
function chatHistory() { try { return JSON.parse(localStorage.getItem(chatKey()) || "[]"); } catch (e) { return []; } }
function saveChat(list) { try { localStorage.setItem(chatKey(), JSON.stringify(list.slice(-12))); } catch (e) { /* без хранилища — до перезагрузки */ } }

function chatBlock() {
  const i = state.insight;
  const others = (state.feed || []).filter((x) => x.event_id !== state.current).slice(0, 25);
  return `<div class="chat"><div class="chat-head"><h4>Задать уточняющий вопрос</h4><button class="link-btn" id="chatClear" hidden>очистить историю</button></div>
    <div class="quick">${chatSuggestions(i).map((t) => `<button data-q="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    <div class="chat-log" id="chatLog"></div>
    <div class="chat-row"><input id="chatInput" aria-label="Ваш вопрос по разбору" placeholder="Например: как это повлияет на наш лизинг?"><button class="btn" id="chatSend">Спросить</button></div>
    ${others.length ? `<details class="cmp"><summary>Сравнить с другим событием: что важнее для компании?</summary>
      <div class="cmp-row"><select id="cmpSel" aria-label="Событие для сравнения">${others.map((x) => `<option value="${esc(x.event_id)}">${esc(x.title.length > 90 ? x.title.slice(0, 90) + "…" : x.title)}</option>`).join("")}</select><button class="btn ghost small" id="cmpGo">Сравнить</button></div>
      <div id="cmpOut"></div></details>` : ""}
    <p class="muted small chat-note">Чат отвечает только по этому разбору, данным компании и аналитике банка. Суммы — из калькулятора, не от модели.</p></div>`;
}

// ---------------------------------------------------------------- 52. мини-график сумм под ответом про деньги
function moneyMini(i) {
  const by = Object.fromEntries((i.scenarios || []).map((s) => [s.name, s]));
  const rows = ["negative", "base", "positive"].filter((n) => by[n] && !by[n].negligible).map((n) => by[n]);
  if (!rows.length) return "";
  const max = Math.max(1, ...rows.map((s) => Math.abs(s.total_rub)));
  return `<div class="cm-money" aria-label="Эффект за 12 месяцев по калькулятору">${rows.map((s) => `<div class="cm-row"><span>${esc(SCEN_SHORT[s.name])}</span>
    <span class="cm-bar"><i class="${s.total_rub < 0 ? "neg" : "pos"}" style="width:${Math.max(2, Math.abs(s.total_rub) / max * 100).toFixed(1)}%"></i></span><b class="${s.total_rub < 0 ? "bad" : s.total_rub > 0 ? "ok" : ""}">${s.name === "base" && !s.total_rub ? "0 ₽" : rub(s.total_rub)}</b></div>`).join("")}
    <div class="muted small">за 12 месяцев · калькулятор${myNums() ? " · по вашим цифрам" : ", оценки по профилю"}</div></div>`;
}

function botTools(k) {
  return `<div class="msg-tools" data-k="${k}"><button class="link-btn" data-style="simple">Объясни проще</button><button class="link-btn" data-style="owner">Для собственника</button>
    <button class="link-btn" data-totask>＋ в мои действия</button><span class="msg-rate"><button class="icon-btn sm" data-rate="1" title="Полезный ответ" aria-label="Полезный ответ">${ico("like")}</button><button class="icon-btn sm" data-rate="-1" title="Неточный ответ" aria-label="Неточный ответ">${ico("dislike")}</button></span></div>`;
}
function drawMsg(m, k) {
  const i = state.insight;
  return `<div class="msg user">${esc(m.q)}${m.style ? `<span class="msg-style">${m.style === "simple" ? "проще" : "для собственника"}</span>` : ""}</div>
    <div class="msg bot ${m.mode === "guard" ? "guard" : ""}" data-k="${k}">${renderChatMd(m.a || "")}${m.attach === "scenarios" && i ? moneyMini(i) : ""}</div>${m.a && m.mode !== "guard" ? botTools(k) : ""}`;
}

function bindChat() {
  if (!$("#chatSend")) return;
  let hist = chatHistory();
  const log = $("#chatLog");
  const redraw = () => { log.innerHTML = hist.map(drawMsg).join(""); bindLog(); $("#chatClear").hidden = !hist.length; };
  const send = async (text, style = null) => {
    if (!text) return;
    track("chat_question", { event_id: state.current, meta: style ? { style } : undefined });
    $("#chatInput").value = "";
    const m = { q: text, a: "", style }; hist.push(m);
    const k = hist.length - 1;
    log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(text)}</div><div class="msg bot typing" data-k="${k}"></div>`);
    const bubble = log.querySelector(`.msg.bot[data-k="${k}"]`);
    try {
      await sse("/api/chat/stream", { method: "POST", body: JSON.stringify({ event_id: state.current, company_id: state.company, role: state.role, question: text, style }) }, (e) => {
        if (e.type === "delta") { bubble.classList.remove("typing"); m.a += e.text; bubble.innerHTML = renderChatMd(m.a); }
        if (e.type === "meta") { m.mode = e.mode; m.attach = e.attach || null; bubble.title = "режим: " + e.mode; }
      });
      saveChat(hist); redraw();
      const last = log.querySelector(`.msg.bot[data-k="${k}"]`); if (last) last.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (e) { hist.pop(); bubble.classList.add("bad"); bubble.textContent = e.message; }
  };
  // 53. тот же вопрос другим языком; 56. ответ → «мои действия»; 58. оценка ответа
  const bindLog = () => {
    log.querySelectorAll(".msg-tools").forEach((t) => {
      const m = hist[+t.dataset.k];
      t.querySelectorAll("[data-style]").forEach((b) => b.onclick = () => send(m.q, b.dataset.style));
      t.querySelector("[data-totask]").onclick = (e) => {
        const plain = (m.a || "").replace(/\[[^\]]*\]/g, "").replace(/\*\*/g, "").replace(/^(Вывод сервиса|Позиция Альфа-Банка)\s*:?\s*/gm, "");
        const line = plain.split("\n").map((x) => x.replace(/^[-•*\d.)\s]+/, "").trim()).find((x) => x.length > 15) || m.q;
        addTask({ text: line.length > 220 ? line.slice(0, 217) + "…" : line }, e.currentTarget);
      };
      t.querySelectorAll("[data-rate]").forEach((b) => b.onclick = () => {
        m.rate = +b.dataset.rate; saveChat(hist);
        t.querySelectorAll("[data-rate]").forEach((x) => x.classList.toggle("on", x === b));
        track("chat_rating", { event_id: state.current, value: m.rate, meta: { mode: m.mode || "", style: m.style || "" } });
        toast(m.rate > 0 ? "Спасибо! Учтём в метриках качества чата" : "Спасибо — ответ отмечен как неточный, разберём");
      });
      if (m.rate) t.querySelectorAll("[data-rate]").forEach((x) => x.classList.toggle("on", +x.dataset.rate === m.rate));
    });
    bindCites(log);
  };
  redraw();
  $("#chatSend").onclick = () => send($("#chatInput").value.trim());
  $("#chatInput").onkeydown = (e) => { if (e.key === "Enter") send($("#chatInput").value.trim()); };
  $("#chatClear").onclick = () => { hist = []; saveChat(hist); redraw(); };
  document.querySelectorAll("#insightPanel [data-q]").forEach((b) => b.onclick = () => send(b.dataset.q));
  const go = document.getElementById("cmpGo");
  if (go) go.onclick = () => { $("#cmpOut").innerHTML = compareEvents(state.current, $("#cmpSel").value); track("chat_question", { event_id: state.current, meta: { compare: 1 } }); bindCmp(); };
}

// ---------------------------------------------------------------- 55. ссылка из ответа → источник с подсвеченной цитатой
function bindCites(root) {
  root.querySelectorAll("span.cite[data-cid]").forEach((el) => {
    el.tabIndex = 0; el.setAttribute("role", "button");
    const go = () => {
      const id = el.dataset.cid;
      renderInsight("sources");
      const li = document.getElementById("src-" + CSS.escape(id));
      if (li) { li.classList.add("hl", "hl-pulse"); li.scrollIntoView({ block: "center", behavior: "smooth" }); }
      track("source_click", { event_id: state.current, meta: { from: "chat" } });
    };
    el.onclick = go; el.onkeydown = (e) => { if (e.key === "Enter") go(); };
  });
}

// ---------------------------------------------------------------- 60. сравнение двух событий: правила сервиса, без модели
function compareEvents(a, b) {
  const A = (state.feed || []).find((x) => x.event_id === a), B = (state.feed || []).find((x) => x.event_id === b);
  if (!A || !B) return `<p class="muted">Событие не найдено в ленте.</p>`;
  const lvl = (x) => impLevel(x).level, mon = (x) => { const m = eventMoney(x.event_id); return m ? Math.max(-m.down, m.up) : 0; };
  const moneyCell = (x) => { const m = eventMoney(x.event_id); return m ? moneyText(m) : "заметного влияния нет"; };
  const date = (x) => x.effective ? `${fmtDate(x.effective.date)} (через ${x.effective.days} ${plural(x.effective.days, "день", "дня", "дней")})` : "—";
  const rows = [["Эффект", (x) => EFFECT[x.effect]], ["Важность для компании", (x) => IMP_RU[lvl(x)]], ["Деньги на кону", moneyCell],
    ["Вступает в силу", date], ["Позиция банка", (x) => x.has_bank_view ? "есть" : "нет"], ["Почему касается", (x) => (x.reasons[0] || {}).why || "—"]];
  let win = null, why = "";
  if (lvl(A) !== lvl(B)) { win = lvl(A) > lvl(B) ? A : B; why = `выше важность для компании: ${IMP_RU[lvl(win)]} против ${IMP_RU[lvl(win === A ? B : A)]}`; }
  else if (Math.abs(mon(A) - mon(B)) > 0) { win = mon(A) > mon(B) ? A : B; why = "при равной важности — больше денег на кону по калькулятору"; }
  else if ((A.importance_score || A.score) !== (B.importance_score || B.score)) { win = (A.importance_score || A.score) > (B.importance_score || B.score) ? A : B; why = "при равной важности — сильнее связь с профилем компании"; }
  const short = (x) => esc(x.title.length > 70 ? x.title.slice(0, 70) + "…" : x.title);
  return `<table class="cmp-t"><thead><tr><th></th><th>Это событие</th><th>Другое</th></tr></thead><tbody>
    <tr><td></td><td><b>${short(A)}</b></td><td><button class="link-btn" data-cmp-open="${esc(B.event_id)}">${short(B)}</button></td></tr>
    ${rows.map(([t, f]) => `<tr><td class="muted small">${t}</td><td>${esc(f(A))}</td><td>${esc(f(B))}</td></tr>`).join("")}</tbody></table>
    <div class="cmp-verdict">${win ? `${ico("spark")} Важнее сейчас: <b>${win === A ? "это событие" : "другое"}</b> — ${esc(why)}.` : "По правилам сервиса события равнозначны — решайте по срокам."}</div>
    <p class="muted small">Сравнение по тем же правилам, что у ленты и «Денег на кону», без модели.</p>`;
}
function bindCmp() { document.querySelectorAll("[data-cmp-open]").forEach((b) => b.onclick = () => openInsight(b.dataset.cmpOpen, false, "compare")); }

// ---------------------------------------------------------------- 54. чат по всей ленте на главной
const FEED_Q = { treasurer: ["Что главное на неделе для казначея?", "Какие события про ставку и курс?"], accountant: ["Какие нормы и налоги меняются?", "Что вступает в силу скоро?"],
  owner: ["Что главное для бизнеса на этой неделе?", "Где самые большие риски?"], default: ["Что главное на этой неделе?", "Где самые большие риски?"] };
function renderFeedChat() {
  const box = document.getElementById("feedChat");
  if (!box) return;
  const qs = [...(FEED_Q[state.role] || FEED_Q.default), "Есть ли возможности для роста?"];
  box.innerHTML = `<div class="mini-head"><h3>${ico("spark")} Спросить про всю ленту</h3></div>
    <div class="quick">${qs.map((t) => `<button data-fq="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    <div class="fc-log" id="fcLog"></div>
    <div class="chat-row"><input id="fcInput" aria-label="Вопрос по всей ленте" placeholder="Например: что важно для закупок?"><button class="btn small" id="fcSend">Спросить</button></div>`;
  const send = async (text) => {
    if (!text) return;
    track("chat_question", { meta: { scope: "feed" } });
    $("#fcInput").value = "";
    const log = $("#fcLog");
    log.innerHTML = `<div class="msg user">${esc(text)}</div><div class="msg bot typing"></div>`;
    const bubble = log.querySelector(".msg.bot");
    let raw = "", evs = [];
    const draw = () => {
      bubble.innerHTML = renderChatMd(raw.replace(/\[ev:(\d+)\]/g, "⟦$1⟧")).replace(/⟦(\d+)⟧/g, (_, n) => {
        const e = evs.find((x) => x.n === +n);
        return e ? `<button class="cite ev" data-fev="${esc(e.event_id)}" title="${esc(e.title)}">событие ${n}</button>` : "";
      });
      bubble.querySelectorAll("[data-fev]").forEach((b) => b.onclick = () => { show("radar"); openInsight(b.dataset.fev, false, "home"); });
    };
    try {
      await sse("/api/chat/feed", { method: "POST", body: JSON.stringify({ company_id: state.company, role: state.role, question: text }) }, (e) => {
        if (e.type === "meta") { evs = e.events || []; bubble.title = "режим: " + e.mode; }
        if (e.type === "delta") { bubble.classList.remove("typing"); raw += e.text; draw(); }
      });
    } catch (e) { bubble.classList.add("bad"); bubble.textContent = e.message; }
  };
  box.querySelectorAll("[data-fq]").forEach((b) => b.onclick = () => send(b.dataset.fq));
  $("#fcSend").onclick = () => send($("#fcInput").value.trim());
  $("#fcInput").onkeydown = (e) => { if (e.key === "Enter") send($("#fcInput").value.trim()); };
}
