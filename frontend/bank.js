// Разделы интернет-банка (счета, кредиты, ВЭД, зарплатный проект) с точками входа в Бизнес-радар.
// Данные — из профиля компании (данные кейса) и калькулятора сервиса; остатков и операций в данных кейса нет,
// поэтому их раздел не выдумывает, а честно показывает, что есть: обороты по контрагентам, продукты, оценки калькулятора.
/* global state, api, q, esc, rub, fmtDate, fmtVal, EFFECT, show, openInsight, openAction, toast, readSet */

const PRODUCT_TITLE = {
  CL_PRODUCT_LOA_MARK: "Кредит", CL_PRODUCT_DEP_MARK: "Депозит", CL_PRODUCT_LEA_MARK: "Лизинг", CL_PRODUCT_ZPP_MARK: "Зарплатный проект",
  CL_PRODUCT_GAR_MARK: "Гарантии", FACTORING_MARK: "Факторинг", FTS_FLAG: "ВЭД",
};

const BANK = {
  accounts: {
    title: "Счета и платежи", factors: ["tax_regulation", "inflation", "consumer_demand", "budget_policy"],
    buttons: [["Скачать выписку", "stub"], ["Импорт платежей", "stub"], ["Создать платёж", "stub"], ["Ещё", "stub"]],
  },
  credits: {
    title: "Кредиты", factors: ["key_rate", "credit_conditions", "bond_market", "real_estate"],
    buttons: [["Рефинансировать кредит", "app:Рефинансирование кредита"], ["Новый кредит", "app:Кредит для бизнеса"],
      ["Обсудить с менеджером", "manager"]],
  },
  ved: {
    title: "ВЭД", factors: ["fx_rub", "sanctions_trade", "china_trade", "logistics", "geopolitics"],
    buttons: [["Валютный контроль", "app:Валютный контроль"], ["Зафиксировать курс", "app:Хеджирование валютного риска"],
      ["Обсудить с менеджером", "manager"]],
  },
  salary: {
    title: "Зарплатный проект", factors: ["labor_market", "tax_regulation", "inflation"],
    buttons: [["Выплатить зарплату", "stub"], ["Подключить сотрудников", "stub"], ["Обсудить с менеджером", "manager"]],
  },
};

function bankButtons(cfg) {
  return cfg.buttons.map(([t, act], i) => `<button class="btn ${i === 0 ? "btn-red" : "ghost"} small" data-bact="${esc(act)}">${esc(t)}</button>`).join("");
}

function bindBankButtons(box) {
  box.querySelectorAll("[data-bact]").forEach((b) => b.onclick = () => {
    const act = b.dataset.bact;
    state.current = null; // обращение из раздела банка, а не из разбора события
    if (act === "manager") return openAction("manager");
    if (act.startsWith("app:")) return openAction("application", act.slice(4));
    toast("Эта операция выполняется в основном интернет-банке Альфа-Бизнес. Здесь — аналитика Бизнес-радара по разделу.");
  });
  box.querySelectorAll("[data-bev]").forEach((el) => {
    const go = () => { show("radar"); openInsight(el.dataset.bev, false, "bank"); };
    el.onclick = go;
    el.onkeydown = (e) => { if (e.key === "Enter") go(); };
  });
  box.querySelectorAll("[data-bgo]").forEach((el) => el.onclick = () => show(el.dataset.bgo));
}

// События ленты, которые касаются раздела: по факторам из причин «почему вам»
function sectionEvents(factors, n = 5) {
  const fs = new Set(factors), read = readSet();
  return (state.feed || []).filter((it) => it.reasons.some((r) => fs.has(r.factor))).slice(0, n).map((it) => `
    <li class="bk-ev ${read.has(it.event_id) ? "read" : ""}" data-bev="${it.event_id}" tabindex="0" role="button">
      <span class="pill ${it.effect}">${EFFECT[it.effect]}</span><b>${esc(it.title)}</b>
      <div class="muted small">${fmtDate(it.published_at)} · ${esc((it.reasons[0] || {}).why || "")}</div></li>`).join("");
}

async function sectionForecasts(factors) {
  if (!state.bankFc) {
    try { state.bankFc = await api("/api/bank/forecasts"); } catch (e) { state.bankFc = { forecasts: [] }; }
  }
  const fs = new Set(factors);
  const rows = state.bankFc.forecasts.filter((f) => fs.has(f.factor) && f.period >= "2026").slice(0, 6).map((f) => {
    const r = f.revision;
    return `<tr><td>${esc(f.title)}, ${esc(f.period)}</td><td class="num">${fmtVal(f.value, f.unit)}</td>
      <td>${r ? `<span class="rev ${r.direction}">${r.direction === "up" ? "▲" : "▼"} было ${fmtVal(r.from, f.unit)}</span>` : ""}</td></tr>`;
  }).join("");
  return rows ? `<div class="card"><div class="eyebrow">Позиция Альфа-Банка</div><h3>Прогноз банка по теме раздела</h3>
    <table class="fc">${rows}</table><div class="muted small">▲▼ — банк пересмотрел прогноз</div></div>` : "";
}

async function scenarioRow(inputs, lever) {
  try {
    const r = await api("/api/scenario", { method: "POST", body: JSON.stringify({ company_id: state.company, role: state.role, inputs }) });
    const row = r.result.rows.find((x) => x.lever === lever);
    return { effect: row ? row.effect_rub : 0, defaults: r.defaults, estimated: r.estimated, baseline: r.baseline };
  } catch (e) { return null; }
}

// Сумма без знака: долг, ФОТ, выручка — это величины, а не эффект «плюс/минус»
function amount(v) { return v == null ? "—" : rub(Math.abs(v)).replace(/^[+−]/, ""); }

function stat(label, value, hint) {
  return `<div class="bk-stat"><span>${esc(label)}</span><b>${value}</b>${hint ? `<i>${esc(hint)}</i>` : ""}</div>`;
}

async function renderBankSection(key) {
  const cfg = BANK[key], p = state.profile || {}, box = document.getElementById("bankBox");
  if (!cfg || !box) return;
  document.getElementById("bankTitle").textContent = cfg.title;
  document.getElementById("bankActions").innerHTML = bankButtons(cfg);
  box.innerHTML = `<div class="skeleton"></div>`;
  const has = (k) => (p.products || []).includes(k);
  const rev = p.revenue && !p.revenue_suspicious ? p.revenue : null;
  let hero = "", side = "";

  if (key === "accounts") {
    const mix = (dir) => (p.industry_mix || []).filter((m) => m.direction === dir).slice(0, 5).map((m) => `
      <div class="peer-row"><span>${esc(m.name)}</span><span class="bar"><i style="width:${Math.round(m.share * 100)}%"></i></span><b>${Math.round(m.share * 100)}%</b></div>`).join("");
    hero = `<div class="bk-hero">
      <div class="eyebrow light">Платёжный поток компании</div>
      <h3>${rev ? amount(rev) : "Выручка не указана"} <small>выручка за год</small></h3>
      <div class="bk-stats">${stat("Покупателей", p.n_buyers ?? "—")}${stat("Поставщиков", p.n_suppliers ?? "—")}
        ${stat("Крупнейший покупатель", p.top_buyer_share != null ? Math.round(p.top_buyer_share * 100) + "%" : "—", "доля поступлений")}
        ${stat("Продукты банка", (p.products || []).length)}</div>
      <div class="bk-chips">${(p.products || []).map((k) => `<span class="chip">${esc(PRODUCT_TITLE[k] || k)}</span>`).join("")}</div></div>`;
    side = `<div class="card"><div class="eyebrow">Граф платежей</div><h3>Кому платите и кто платит вам</h3>
      <div class="sub-h">Поступления — по отраслям покупателей</div>${mix("in") || `<div class="muted small">нет данных о платежах</div>`}
      <div class="sub-h">Платежи — по отраслям поставщиков</div>${mix("out") || `<div class="muted small">нет данных о платежах</div>`}
      ${p.hhi_in != null ? `<div class="muted small">Концентрация покупателей: ${p.hhi_in > 0.25 ? "высокая — зависимость от немногих покупателей" : "умеренная"}</div>` : ""}</div>`;
  }

  if (key === "credits") {
    const s = await scenarioRow({ rate_delta_pp: 1 }, "rate");
    const debt = s && s.defaults.debt_rub;
    const loan = has("CL_PRODUCT_LOA_MARK") || has("CL_PRODUCT_LEA_MARK");
    const kr = s && s.baseline && s.baseline.key_rate_now;
    hero = `<div class="bk-hero">
      <div class="eyebrow light">${loan ? "Кредиты и лизинг компании" : "Кредитов в банке нет"}</div>
      <h3>${debt ? amount(debt) : "—"} <small>${debt ? "долг, оценка по профилю" : "долга в банке нет"}</small></h3>
      <div class="bk-stats">${stat("Ключевая ставка", kr != null ? String(kr).replace(".", ",") + "%" : "—", "по материалам Альфа-Банка")}
        ${stat("Ставка +1 п.п. к прогнозу банка", s && s.effect ? rub(s.effect) : "—", "за 12 месяцев, по калькулятору")}
        ${stat("Плавающая часть долга", s && s.defaults.floating_share != null ? Math.round(s.defaults.floating_share * 100) + "%" : "—", "оценка, можно уточнить в разборе")}</div>
      ${s && s.estimated.length ? `<div class="bk-note">Как оценено: ${esc(s.estimated.join("; "))}. Свои цифры можно ввести в калькуляторе «Что если» в любом разборе.</div>` : ""}</div>`;
  }

  if (key === "ved") {
    const s = await scenarioRow({ fx_change_pct: 10 }, "fx");
    hero = `<div class="bk-hero">
      <div class="eyebrow light">${p.fts ? "Компания ведёт ВЭД" : "ВЭД в данных компании нет"}</div>
      <h3>${(p.fts_countries || []).length ? esc(p.fts_countries.slice(0, 4).join(", ")) : "—"} <small>страны-партнёры</small></h3>
      <div class="bk-stats">${stat("Импорт", s && s.defaults.import_rub ? amount(s.defaults.import_rub) : "—", "оценка по профилю")}
        ${stat("Рубль слабее на 10%", s && s.effect ? rub(s.effect) : "—", "за 12 месяцев, по калькулятору")}
        ${stat("Перенос в цены", s && s.defaults.pass_through != null ? Math.round(s.defaults.pass_through * 100) + "%" : "—", "доля удорожания, которую компания переносит в цены")}</div></div>`;
  }

  if (key === "salary") {
    const s = await scenarioRow({}, "wages");
    const pay = s && s.defaults.payroll_rub;
    hero = `<div class="bk-hero">
      <div class="eyebrow light">${has("CL_PRODUCT_ZPP_MARK") ? "Зарплатный проект подключён" : "Зарплатный проект не подключён"}</div>
      <h3>${pay ? amount(pay) : "—"} <small>фонд оплаты труда за год, оценка</small></h3>
      <div class="bk-stats">${stat("Сотрудников", esc(p.workers || "—"))}
        ${stat("Рост зарплат на 1 п.п.", pay ? rub(-Math.round(pay * 0.01)) : "—", "≈ 1% ФОТ за год")}</div>
      ${s && s.estimated.length ? `<div class="bk-note">Как оценено: ${esc(s.estimated.filter((x) => x.startsWith("ФОТ")).join("; ") || "по численности")}.</div>` : ""}</div>`;
  }

  const evs = sectionEvents(cfg.factors);
  const fc = await sectionForecasts(cfg.factors);
  box.innerHTML = `${hero}
    <div class="bk-grid">
      <div class="card"><div class="eyebrow">Бизнес-радар</div><h3>Что в новостях касается раздела «${esc(cfg.title)}»</h3>
        ${evs ? `<ul class="bk-evs">${evs}</ul>` : `<p class="muted">Сейчас в ленте нет событий по теме раздела — менять планы не нужно.</p>`}
        <button class="btn ghost small" data-bgo="radar">Вся лента →</button></div>
      <div class="bk-col">${side}${fc}</div>
    </div>`;
  bindBankButtons(document.getElementById("view-bank"));
}

function openBank(key) {
  state.bank = key;
  document.querySelectorAll(".nav-item[data-bank]").forEach((n) => n.classList.toggle("active", n.dataset.bank === key));
  renderBankSection(key);
}
