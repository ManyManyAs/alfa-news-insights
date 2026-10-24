// Этап 8: агент и прозрачность — схема шагов агента, «что исправил критик» (черновик против итога), почему такая важность,
// безопасность, бюджет с прогнозом, сравнение моделей, выгрузка трассировки, отбор событий для «Денег на кону».

// ---------------------------------------------------------------- 71, 77. схема шагов: узлы со временем, моделью и стоимостью
const AG_NODES = [
  ["relevance", "Релевантность", (n) => n === "relevance"],
  ["plan", "План", (n) => n === "plan"],
  ["tools", "Инструменты", (n) => n.startsWith("tool:") && !n.includes("(retry)")],
  ["draft", "Черновик", (n) => n === "draft"],
  ["critic", "Критик", (n) => /^critique:.*#1$/.test(n)],
  ["decide", "Решение", (n) => n === "decide:revise"],
  ["revise", "Правка", (n) => n === "revise" || n === "repair:rules" || n.includes("(retry)")],
  ["check2", "Проверка", (n) => /^critique:.*#2$/.test(n)],
  ["final", "Итог", (n) => n === "finalize"],
];
const fmtMs = (ms) => ms >= 1000 ? `${(ms / 1000).toFixed(1).replace(".", ",")} с` : `${ms} мс`;
function agentGraph(steps) {
  const nodes = AG_NODES.map(([k, t, f]) => {
    const st = steps.filter((s) => f(s.name));
    const ms = st.reduce((a, s) => a + (s.duration_ms || 0), 0), cost = st.reduce((a, s) => a + (s.cost_rub || 0), 0);
    const llm = st.find((s) => s.kind === "llm" && s.model);
    return { k, t, n: st.length, ms, cost, model: llm ? llm.model.split("/").pop().replace(/-flash|-instruct|-chat/g, "") : st.length ? (st.some((s) => s.kind === "tool") ? "инструменты" : "правила") : "",
      retry: st.some((s) => s.name.includes("(retry)")), kind: llm ? "llm" : st.some((s) => s.kind === "tool") ? "tool" : "rules" };
  });
  const dec = steps.find((s) => s.name === "decide:revise"), rev = dec && dec.output ? dec.output.revise_with_llm : null;
  const W = 118, G = 14, H = 74, cols = nodes.length, width = cols * W + (cols - 1) * G;
  const box = (nd, i) => { const x = i * (W + G), off = !nd.n;
    return `<g class="ag-n ${nd.kind} ${off ? "off" : ""}" transform="translate(${x},14)"><rect width="${W}" height="${H}" rx="12"/>
      <text x="10" y="20" class="ag-t">${esc(nd.t)}${nd.n > 1 ? ` ×${nd.n}` : ""}</text>
      <text x="10" y="40" class="ag-m">${off ? "не понадобилось" : esc(nd.model.slice(0, 16))}</text>
      ${off ? "" : `<text x="10" y="60" class="ag-s">${fmtMs(nd.ms)}${nd.cost ? ` · ${nd.cost.toFixed(2).replace(".", ",")} ₽` : ""}</text>`}
      ${nd.retry ? `<g transform="translate(${W - 18},-8)"><circle r="9" class="ag-badge"/><text text-anchor="middle" y="4" class="ag-bt">↻</text></g>` : ""}</g>`; };
  const arrows = nodes.slice(1).map((nd, i) => { const x = (i + 1) * (W + G) - G; return `<path d="M${x + 1} ${14 + H / 2}h${G - 4}" class="ag-a"/><path d="M${x + G - 7} ${14 + H / 2 - 4}l4 4-4 4" class="ag-a"/>`; }).join("");
  const loop = nodes[6].n ? `<path d="M${6 * (W + G) + W / 2} ${14 + H}v14H${4 * (W + G) + W / 2}v-14" class="ag-loop"/><text x="${5 * (W + G) + W / 2}" y="${14 + H + 26}" text-anchor="middle" class="ag-lt">замечания критика → правка → повторная проверка</text>` : "";
  const retry = nodes.find((x) => x.retry);
  return `<div class="ag-wrap"><svg class="ag-svg" viewBox="-4 0 ${width + 8} ${H + 50}" role="img" aria-label="Схема шагов агента">${arrows}${nodes.map(box).join("")}${loop}</svg></div>
    <div class="ag-notes small">${dec ? `<span>${ico("spark")} Решение о правке: <b>${rev ? "править моделью" : "без правки моделью"}</b> — ${esc(dec.output.why || "")}</span>` : ""}
      ${retry ? `<span>↻ Агент не нашёл позицию банка с первого раза и повторил поиск с расширенным запросом.</span>` : ""}</div>`;
}

// ---------------------------------------------------------------- 72. что исправил критик: черновик против итога
const DIFF_SECTIONS = [["headline", "Заголовок"], ["takeaway", "Главный вывод"], ["what_happened", "Что произошло"], ["why_it_matters", "Почему важно"],
  ["risks", "Риски"], ["opportunities", "Возможности"], ["actions", "Действия"], ["bank_view", "Позиция банка"]];
function claimTexts(ins, k) {
  if (!ins) return [];
  if (k === "headline") return ins.headline ? [ins.headline] : [];
  if (k === "takeaway") return ins.takeaway && ins.takeaway.text ? [ins.takeaway.text] : [];
  const list = k === "risks" || k === "opportunities" ? (ins.consequences || {})[k] : ins[k];
  return (list || []).map((c) => c.text).filter(Boolean);
}
const normT = (t) => String(t || "").toLowerCase().replace(/\[[^\]]*\]/g, "").replace(/[^а-яёa-z0-9%]+/g, " ").trim();
function draftDiff(steps, final) {
  const dr = steps.find((s) => s.name === "draft");
  const draft = dr && dr.output && dr.output.headline ? dr.output : null;
  const issues = steps.filter((s) => /^critique:.*#1$/.test(s.name)).flatMap((s) => (s.output && (s.output.issues || [])) || []);
  if (!draft) return "";
  const rows = DIFF_SECTIONS.map(([k, t]) => {
    const a = claimTexts(draft, k), b = claimTexts(final, k), na = new Set(a.map(normT)), nb = new Set(b.map(normT));
    const gone = a.filter((x) => !nb.has(normT(x))), added = b.filter((x) => !na.has(normT(x)));
    return gone.length || added.length ? `<div class="df-sec"><div class="sub-h">${t}</div>${gone.map((x) => `<div class="df-del">− ${esc(humanText(x))}</div>`).join("")}${added.map((x) => `<div class="df-add">+ ${esc(humanText(x))}</div>`).join("")}</div>` : "";
  }).join("");
  const iss = [...new Set(issues)].slice(0, 6);
  return `<details class="df-wrap" ${rows ? "open" : ""}><summary>Что исправил критик: черновик модели против итога</summary>
    ${iss.length ? `<div class="df-issues"><div class="sub-h">Замечания проверки</div><ul>${iss.map((x) => `<li>${esc(humanText(x))}</li>`).join("")}</ul></div>` : ""}
    ${rows || `<p class="muted small">Черновик прошёл проверку без смысловых правок — итог совпадает с черновиком.</p>`}
    <p class="muted small">Удалённое из черновика — красным, добавленное — зелёным. Суммы и сценарии не сравниваем: их считает калькулятор.</p></details>`;
}
// технические ссылки в тексте черновика → слова (как в остальном интерфейсе)
function humanText(t) { return typeof cleanRefs === "function" ? cleanRefs(t) : String(t || ""); }

// ---------------------------------------------------------------- 78. выгрузка трассировки JSON
function exportTrace(traceId, steps) {
  const blob = new Blob([JSON.stringify({ trace_id: traceId, exported_at: new Date().toISOString(), steps }, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `trace-${traceId}.json`;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast("Трассировка выгружена одним файлом JSON");
}

// ---------------------------------------------------------------- 73. почему такая важность
function impExplain(it) {
  const m = it && it.imp;
  if (!m || m.rules_level == null) return "";
  const th = m.thresholds || {}, sc = String(m.score).replace(".", ",");
  const prof = `По профилю компании связь ${sc}: ${m.rules_level === 3 ? `не ниже ${String(th.high).replace(".", ",")} — «высокая»` : m.rules_level === 2 ? `от ${String(th.mid).replace(".", ",")} до ${String(th.high).replace(".", ",")} — «средняя»` : `ниже ${String(th.mid).replace(".", ",")} — «низкая»`}.`;
  const ai = m.source === "ai" ? (m.capped ? `Модель после разбора оценила выше («${IMP_RU[m.ai_level]}»), но сервис не поднимает важность больше чем на ступень над профилем.`
    : m.level < m.rules_level ? `Модель после разбора понизила: «${m.label}»${m.why ? ` — ${m.why}` : ""}.` : m.level > m.rules_level ? `Модель после разбора подняла на ступень: «${m.label}»${m.why ? ` — ${m.why}` : ""}.` : `Модель после разбора подтвердила${m.why ? `: ${m.why}` : ""}.`) : "Полного разбора моделью ещё нет — важность по профилю.";
  const reasons = (it.reasons || []).slice(0, 3).map((r) => `<li><b>${esc(r.factor_title || r.factor)}</b>${r.contribution != null ? ` · вклад ${String(Math.round(r.contribution * 100) / 100).replace(".", ",")}` : ""}${r.why ? ` — ${esc(r.why)}` : ""}</li>`).join("");
  return `<details class="imp-x"><summary>Почему важность «${esc(m.label)}»</summary><p>${esc(prof)}</p><p>${esc(ai)}</p>${reasons ? `<ul>${reasons}</ul>` : ""}</details>`;
}

// ---------------------------------------------------------------- 74, 75, 76, 79. для команды банка: безопасность, бюджет, модели, отбор денег
async function renderOps(box) {
  if (!box) return;
  let p = null, b = null;
  try { [p, b] = await Promise.all([api("/api/proof"), api("/api/llm/budget")]); } catch (e) { return; }
  const n2 = (x) => x == null ? "—" : Number(x).toFixed(2).replace(".", ",");
  // бюджет: темп с последнего сброса (03:00 МСК) и прогноз на конец бюджетных суток
  const now = new Date(), msk = new Date(now.getTime() + (now.getTimezoneOffset() + 180) * 6e4);
  const since = (msk.getHours() + msk.getMinutes() / 60 - 3 + 24) % 24 || 0.1, left = 24 - since;
  const rate = b.spent_rub / Math.max(0.5, since), proj = Math.min(b.budget_rub, b.spent_rub + rate * left);
  const pc = (x) => Math.min(100, x / b.budget_rub * 100).toFixed(1);
  const budget = `<div class="card ops-card"><div class="mini-head"><h3>Бюджет модели сегодня</h3><span class="muted small">сброс в 03:00 МСК · ${esc(b.source === "provider" ? "данные провайдера" : "локальный учёт")}</span></div>
    <div class="bud-bar"><i class="sp" style="width:${pc(b.spent_rub)}%"></i><i class="pj" style="left:${pc(b.spent_rub)}%;width:${(pc(proj) - pc(b.spent_rub)).toFixed(1)}%"></i><s style="left:${pc(b.background_limit_rub)}%" title="Лимит фоновых задач"></s></div>
    <div class="bud-leg small"><span><i class="sp"></i>потрачено ${n2(b.spent_rub)} ₽</span><span><i class="pj"></i>прогноз к сбросу ≈ ${n2(proj)} ₽ при нынешнем темпе (${n2(rate)} ₽/ч)</span><span><i class="bg"></i>фоновые задачи — до ${n2(b.background_limit_rub)} ₽</span><span>из ${n2(b.budget_rub)} ₽</span></div>
    <p class="muted small">${proj >= b.budget_rub * 0.95 ? "При таком темпе бюджет кончится до сброса — сервис переключится на запасные модели, затем на правила." : "Запас есть: при нынешнем темпе бюджет до сброса не кончится."}</p></div>`;
  const red = p.redteam || {};
  const sec = `<div class="card ops-card"><div class="mini-head"><h3>${ico("warn")} Безопасность: атаки отбиты</h3><span class="muted small">${red.passed} из ${red.total} · ${esc(red.run_at || "")}</span></div>
    <ul class="sec-list">${(red.attacks || []).map((a) => `<li class="${a.passed ? "ok" : "bad"}"><span>${a.passed ? "✓" : "✕"}</span><b>${esc(a.title)}</b><span class="muted small">${esc(a.kind || "")} · ${a.checks} проверок</span></li>`).join("")}</ul>
    <p class="muted small">Живой прогон через реальные модели: инъекции в тексте новостей, подмена позиции банка, чужие данные, вывод ключа. Плюс офлайн-тесты в CI на каждый PR.</p></div>`;
  const md = p.models;
  const models = md ? `<div class="card ops-card"><div class="mini-head"><h3>Сравнение моделей агента</h3><span class="muted small">${md.n} разборов · ${esc(md.run_at)}</span></div>
    <table class="mdl"><thead><tr><th></th><th>Смешанный набор</th><th>Только DeepSeek</th></tr></thead><tbody>
      <tr><td>Судья GLM, 1–5</td><td>${n2(md.mixed.glm)}</td><td><b>${n2(md.deepseek.glm)}</b></td></tr>
      <tr><td>Судья Kimi, 1–5</td><td>${n2(md.mixed.kimi)}</td><td><b>${n2(md.deepseek.kimi)}</b></td></tr>
      <tr><td>Обоснованность (GLM / Kimi)</td><td>${md.mixed.grounding.map(n2).join(" / ")}</td><td><b>${md.deepseek.grounding.map(n2).join(" / ")}</b></td></tr>
      <tr><td>Время разбора, p50</td><td>—</td><td>${md.deepseek.seconds_p50 != null ? `${md.deepseek.seconds_p50} с` : "—"}</td></tr>
      <tr><td>Стоимость разбора</td><td>—</td><td>${md.deepseek.rub_mean != null ? `${n2(md.deepseek.rub_mean)} ₽` : "—"}</td></tr></tbody></table>
    <p class="muted small">По итогам сравнения агент переведён на DeepSeek (план, разбор, критик, правка). Отчёт: docs/llm/deepseek_only.md.</p></div>` : "";
  const mv = state.money;
  const money = mv && mv.ai ? `<div class="card ops-card"><div class="mini-head"><h3>ИИ-отбор для «Денег на кону»</h3><span class="muted small">текущая компания</span></div>
    <div class="ms-row">${[["кандидатов", mv.candidates], ["взято в расчёт", mv.events_used], ["отсеяно", mv.dropped]].map(([t, v]) => `<div><b>${v ?? "—"}</b><span class="muted small">${t}</span></div>`).join("")}</div>
    ${(mv.rejected || []).length ? `<ul class="ms-rej">${mv.rejected.slice(0, 4).map((r) => `<li><span>${esc(r.title || "")}</span><span class="muted small">${esc(r.why || r.reason || "")}</span></li>`).join("")}</ul>` : ""}
    <p class="muted small">Модель решает, какие события влияют на деньги и в какую сторону, — суммы считает калькулятор. Отсеянное показано с причиной.</p></div>` : "";
  box.innerHTML = `<h3 class="mon-h">Для команды банка: агент изнутри</h3><div class="ops-grid">${budget}${sec}${models}${money}</div>`;
}
