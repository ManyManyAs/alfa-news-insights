// Визуализации без библиотек (SVG): карта влияния, радар факторов, таймлайн агента.
/* global esc, EFFECT */

const EFFECT_COLOR = { risk: "var(--risk)", opportunity: "var(--opp)", mixed: "var(--mixed)", watch: "var(--watch)" };
const BASIS_RU = {
  CL_PRODUCT_LOA_MARK: "Кредит в банке", CL_PRODUCT_LEA_MARK: "Лизинг", CL_PRODUCT_DEP_MARK: "Депозиты",
  CL_PRODUCT_ZPP_MARK: "Зарплатный проект", CL_PRODUCT_GAR_MARK: "Банковские гарантии", CL_PRODUCT_RKO_MARK: "РКО",
  FACTORING_MARK: "Факторинг", FTS_FLAG: "ВЭД", REGION: "Регион компании",
};
const SVG_NS = "http://www.w3.org/2000/svg";

function wrapText(text, max = 24, lines = 2) {
  const words = String(text).split(/\s+/);
  const out = [""];
  for (const w of words) {
    if ((out[out.length - 1] + " " + w).trim().length > max && out[out.length - 1]) {
      if (out.length === lines) { out[out.length - 1] = out[out.length - 1].replace(/.{0,2}$/, "…"); break; }
      out.push(w);
    } else out[out.length - 1] = (out[out.length - 1] + " " + w).trim();
  }
  return out;
}

function basisLabel(id, sources) {
  const src = (sources || []).find((s) => s.id === id);
  if (src) return src.title.replace(/^Граф платежей: /, "");
  const [kind, ref = ""] = id.split(":");
  if (kind === "profile") {
    if (ref.startsWith("OKVED")) return "Отрасль компании (ОКВЭД " + ref.split("=")[1] + ")";
    if (ref.startsWith("FTS_COUNTRY")) return "Страны ВЭД";
    return BASIS_RU[ref] || ref;
  }
  if (kind === "graph") return id.startsWith("graph:region") ? "Регион контрагентов: " + id.split(":")[2] : "Контрагенты, ОКВЭД " + id.split(":")[2];
  if (kind === "peers") return "Похожие компании: " + (BASIS_RU[ref] || ref);
  if (kind === "user") return "Ваша настройка ленты";
  return id;
}

// Карта влияния: событие → фактор → признак компании → эффект. Толщина связи — вклад в релевантность.
function impactMap(ins) {
  const reasons = (ins.relevance && ins.relevance.reasons || []).filter((r) => r.contribution > 0).slice(0, 5);
  if (!reasons.length) return "";
  const factors = [], bases = [], effects = [];
  const idx = (arr, key, make) => { let i = arr.findIndex((x) => x.key === key); if (i < 0) { arr.push({ key, ...make(), rs: [] }); i = arr.length - 1; } return i; };
  const links = [];
  reasons.forEach((r, ri) => {
    const f = idx(factors, r.factor, () => ({ label: r.factor_title + (r.direction && r.direction !== "unknown" ? " " + ({ up: "↑", down: "↓", stable: "→" }[r.direction] || "") : ""), why: r.evidence || "" }));
    factors[f].rs.push(ri);
    const bs = [...(r.basis || []).filter((b) => b !== "baseline"), ...(r.indirect || []).map((x) => x.basis)];
    const uniq = [...new Set(bs)].slice(0, 2);
    const bIdx = (uniq.length ? uniq : ["macro"]).map((b) => {
      const i = idx(bases, b, () => ({ label: b === "macro" ? "Макрофон: общий для всех компаний" : basisLabel(b, ins.sources), indirect: b.startsWith("graph:") || b.startsWith("peers:") }));
      bases[i].rs.push(ri); return i;
    });
    const e = idx(effects, r.effect, () => ({ label: EFFECT[r.effect] || r.effect, effect: r.effect }));
    effects[e].rs.push(ri);
    links.push({ ri, f, bIdx, e, w: r.contribution, effect: r.effect, why: r.why });
  });
  const W = 700, colX = [0, 182, 374, 576], nodeW = [150, 160, 170, 124], rowH = 62;
  const H = Math.max(factors.length, bases.length, effects.length, 1) * rowH + 24;
  const ys = (n, i) => 12 + (H - 24) * (i + 0.5) / n;
  const maxW = Math.max(...links.map((l) => l.w));
  const sw = (w) => 2 + 7 * w / maxW;
  const path = (x1, y1, x2, y2) => { const mx = (x1 + x2) / 2; return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`; };
  let edges = "";
  const evY = H / 2;
  links.forEach((l) => {
    const fy = ys(factors.length, l.f), col = EFFECT_COLOR[l.effect] || "var(--watch)";
    edges += `<path class="im-edge" data-r="${l.ri}" d="${path(colX[0] + nodeW[0], evY, colX[1], fy)}" stroke="${col}" stroke-width="${sw(l.w)}"/>`;
    l.bIdx.forEach((b) => {
      const by = ys(bases.length, b);
      edges += `<path class="im-edge" data-r="${l.ri}" d="${path(colX[1] + nodeW[1], fy, colX[2], by)}" stroke="${col}" stroke-width="${sw(l.w)}"${bases[b].indirect ? ' stroke-dasharray="6 4"' : ""}/>`;
      edges += `<path class="im-edge" data-r="${l.ri}" d="${path(colX[2] + nodeW[2], by, colX[3], ys(effects.length, l.e))}" stroke="${col}" stroke-width="${sw(l.w)}"/>`;
    });
  });
  const node = (x, y, w, lines, cls, rs, tip) => `<g class="im-node ${cls}" data-r="${rs.join(" ")}" tabindex="0"><title>${esc(tip || lines.join(" "))}</title>
    <rect x="${x}" y="${y - 25}" width="${w}" height="50" rx="10"/>
    <text x="${x + 10}" y="${y - (lines.length - 1) * 8 + 4}">${lines.map((t, i) => `<tspan x="${x + 10}" dy="${i ? 16 : 0}">${esc(t)}</tspan>`).join("")}</text></g>`;
  let nodes = node(colX[0], evY, nodeW[0], wrapText(ins.headline.replace(/^[^:]+:\s*/, ""), 19), "ev", reasons.map((_, i) => i), ins.headline);
  factors.forEach((f, i) => { nodes += node(colX[1], ys(factors.length, i), nodeW[1], wrapText(f.label, 20), "fa", f.rs, f.why); });
  bases.forEach((b, i) => { nodes += node(colX[2], ys(bases.length, i), nodeW[2], wrapText(b.label, 21), "ba" + (b.indirect ? " ind" : ""), b.rs, reasons.filter((_, ri) => b.rs.includes(ri)).map((r) => r.why).join("; ")); });
  effects.forEach((e, i) => { nodes += node(colX[3], ys(effects.length, i), nodeW[3], wrapText(e.label, 15), "ef " + e.effect, e.rs); });
  const heads = ["Событие", "Фактор", "Признак компании", "Эффект"].map((t, i) => `<span style="left:${colX[i] / W * 100}%">${t}</span>`).join("");
  return `<div class="impact"><div class="im-heads">${heads}</div>
    <svg viewBox="0 0 ${W} ${H}" class="im-svg" role="img" aria-label="Карта влияния события на компанию">${edges}${nodes}</svg>
    <div class="muted small">Наведите на узел, чтобы увидеть путь. Толщина — вклад в релевантность; пунктир — влияние через контрагентов или похожие компании.</div></div>`;
}

function bindImpactMap(root) {
  const svg = root.querySelector(".im-svg");
  if (!svg) return;
  const all = svg.querySelectorAll(".im-edge, .im-node");
  const on = (rs) => all.forEach((el) => {
    const mine = el.dataset.r.split(" ").some((r) => rs.includes(r));
    el.classList.toggle("dim", !mine);
  });
  const off = () => all.forEach((el) => el.classList.remove("dim"));
  svg.querySelectorAll(".im-node").forEach((n) => {
    const rs = n.dataset.r.split(" ");
    n.addEventListener("mouseenter", () => on(rs)); n.addEventListener("focus", () => on(rs));
    n.addEventListener("mouseleave", off); n.addEventListener("blur", off);
  });
}

// Радар факторов: чувствительность компании (контур) и сигналы ленты (точки: риск / возможность)
function factorRadar(exposures, feed) {
  const ax = exposures.filter((e) => e.weight > 0).slice(0, 6);
  if (ax.length < 3) return "";
  const sig = {};
  for (const it of feed) for (const r of it.reasons) {
    if (!sig[r.factor]) sig[r.factor] = { risk: 0, opportunity: 0, other: 0 };
    const k = r.effect === "risk" ? "risk" : r.effect === "opportunity" ? "opportunity" : "other";
    sig[r.factor][k] += r.contribution;
  }
  const S = 300, c = S / 2, R = 92, max = Math.max(...ax.map((e) => e.weight));
  const pt = (i, v) => { const a = -Math.PI / 2 + 2 * Math.PI * i / ax.length; return [c + Math.cos(a) * R * v, c + Math.sin(a) * R * v]; };
  let g = "";
  for (const lv of [0.33, 0.66, 1]) g += `<polygon class="rd-grid" points="${ax.map((_, i) => pt(i, lv).join(",")).join(" ")}"/>`;
  ax.forEach((e, i) => {
    const [x, y] = pt(i, 1), [lx, ly] = pt(i, 1.32);
    g += `<line class="rd-axis" x1="${c}" y1="${c}" x2="${x}" y2="${y}"/>`;
    const lines = wrapText(e.title.replace(/ и .*/, ""), 14);
    g += `<text class="rd-label" x="${lx}" y="${ly - (lines.length - 1) * 6}" text-anchor="middle">${lines.map((t, k) => `<tspan x="${lx}" dy="${k ? 12 : 0}">${esc(t)}</tspan>`).join("")}</text>`;
  });
  g += `<polygon class="rd-shape" points="${ax.map((e, i) => pt(i, e.weight / max).join(",")).join(" ")}"/>`;
  const sMax = Math.max(0.01, ...Object.values(sig).map((s) => s.risk + s.opportunity + s.other));
  ax.forEach((e, i) => {
    const s = sig[e.factor];
    if (!s) return;
    const tot = s.risk + s.opportunity + s.other, [x, y] = pt(i, e.weight / max);
    const col = s.risk > s.opportunity ? "var(--risk)" : s.opportunity > s.risk ? "var(--opp)" : "var(--watch)";
    g += `<circle class="rd-dot" cx="${x}" cy="${y}" r="${4 + 8 * tot / sMax}" fill="${col}"><title>${esc(e.title)}: в ленте ${s.risk > s.opportunity ? "преобладают риски" : s.opportunity > s.risk ? "преобладают возможности" : "есть сигналы"}</title></circle>`;
  });
  return `<svg viewBox="0 0 ${S} ${S}" class="radar-svg" role="img" aria-label="Чувствительность компании к факторам и сигналы ленты">${g}</svg>`;
}

// Таймлайн агента: шаги на оси времени, цвет — тип шага, подписи — модель и стоимость
function traceTimeline(steps) {
  if (!steps.length) return "";
  const total = steps.reduce((a, s) => a + (s.duration_ms || 0), 0) || 1;
  const cost = steps.reduce((a, s) => a + (s.cost_rub || 0), 0);
  let t = 0;
  const rows = steps.map((s) => {
    const left = t / total * 100, w = Math.max(0.6, (s.duration_ms || 0) / total * 100);
    t += s.duration_ms || 0;
    return `<div class="tl-row"><span class="tl-name">${esc(typeof stepTitle === "function" ? stepTitle(s.name) : s.name)}</span>
      <span class="tl-track"><i class="tl-bar ${esc(s.kind)}" style="left:${left}%;width:${w}%"></i></span>
      <span class="tl-meta muted small">${s.duration_ms} мс${s.model ? " · " + esc(s.model.split("/").pop()) : ""}${s.cost_rub ? " · " + s.cost_rub.toFixed(3).replace(".", ",") + " ₽" : ""}</span></div>`;
  }).join("");
  return `<div class="tl"><div class="tl-sum">${steps.length} шагов · ${(total / 1000).toFixed(1).replace(".", ",")} с · ${cost.toFixed(2).replace(".", ",")} ₽
    <span class="tl-legend"><i class="llm"></i>модель <i class="tool"></i>инструмент <i class="rules"></i>правила</span></div>${rows}</div>`;
}
