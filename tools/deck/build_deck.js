// Презентация для сдачи (10 слайдов): node tools/deck/build_deck.js <папка-вывода>
// Цифры берутся из файлов репозитория (baseline метрик, судьи, red-team), скриншоты — из docs/img.
// Нужны pptxgenjs, react, react-dom, react-icons, sharp (npm). Тему пишет apply_theme из навыка pptx, если путь задан в APPLY_THEME.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");

const ROOT = path.resolve(__dirname, "..", "..");
const OUT = path.resolve(process.argv[2] || path.join(ROOT, "docs", "presentation"));
const IMG = path.join(ROOT, "docs", "img");
const readJson = (p, d) => { try { return JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf8")); } catch (e) { return d; } };
const num = (x, n = 3) => String(+x.toFixed(n)).replace(".", ",");

// ---------- данные из репозитория
const base = readJson("docs/quality/baseline.json", {});
const judges = readJson("docs/llm/judges.json", null);
const red = readJson("docs/llm/redteam_live.json", null);
// доля оценок двух судей, совпавших с точностью ±1 балл: диапазон по осям, из отчёта judges.json
function within1(j) {
  const v = Object.values((j && j.agreement && j.agreement.axes) || {}).map((x) => Math.round(x.within_1 * 100));
  if (!v.length) return "—";
  const lo = Math.min(...v), hi = Math.max(...v);
  return lo === hi ? `${lo}%` : `${lo}–${hi}%`;
}
const TEAM = (process.env.DECK_TEAM || "@ManyManyAs · @Takabe11").trim();
const STAND = "https://77-221-139-69.sslip.io";
const REPO = "github.com/ManyManyAs/alfa-news-insights";

const THEME = {
  name: "Бизнес-радар", headFontFace: "Arial", bodyFontFace: "Arial",
  colors: { dk1: "0B0B0D", lt1: "FFFFFF", dk2: "2B2B30", lt2: "F3F3F5", accent1: "EF3124", accent2: "2558B8",
    accent3: "6F4AD1", accent4: "1F8A4C", accent5: "8A8A93", accent6: "C4170C", hlink: "2558B8", folHlink: "6F4AD1" },
};
const HEX = { red: "EF3124", ink: "0B0B0D", ink2: "2B2B30", muted: "5C5C66", light: "F3F3F5", line: "DCDCE2", blue: "2558B8",
  violet: "6F4AD1", green: "1F8A4C", white: "FFFFFF" };

async function icon(Comp, color) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Comp, { color: "#" + color, size: 256 }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.33 × 7.5
  pres.title = "Бизнес-радар — Альфа-Хакатон 2026";
  pres.author = "Команда «Бизнес-радар»";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;
  const W = 13.333;

  pres.defineSlideMaster({
    title: "Обложка", background: { color: HEX.ink },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.7, y: 2.0, w: 6.6, h: 1.6, fontSize: 54, bold: true, color: C.background1,
        valign: "bottom", align: "left", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: 0.7, y: 3.8, w: 6.2, h: 1.4, fontSize: 22, color: C.background1, margin: 0,
        valign: "top", align: "left" }, text: "" } },
    ],
  });
  pres.defineSlideMaster({
    title: "Контент", background: { color: HEX.white },
    margin: [0.5, 0.6, 0.6, 0.6],
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: W - 1.2, h: 0.9, fontSize: 28, bold: true,
        color: C.text1, valign: "middle", align: "left", margin: 0 }, text: "" } },
      { text: { text: "Бизнес-радар · Альфа-Хакатон 2026", options: { x: 0.6, y: 7.0, w: 6, h: 0.3, fontSize: 10, color: C.accent5, margin: 0 } } },
    ],
    slideNumber: { x: W - 1.1, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: "8A8A93", align: "right" },
  });

  const ic = {
    news: await icon(fa.FaNewspaper, HEX.red), link: await icon(fa.FaProjectDiagram, HEX.red), rub: await icon(fa.FaRubleSign, HEX.red),
    shield: await icon(fa.FaShieldAlt, HEX.white), lock: await icon(fa.FaUserLock, HEX.white), bug: await icon(fa.FaBug, HEX.white),
    key: await icon(fa.FaKey, HEX.white), globe: await icon(fa.FaGlobe, HEX.white),
  };

  // ---------- 1. Обложка
  pres.addSection({ title: "Начало" });
  let s = pres.addSlide({ masterName: "Обложка", sectionTitle: "Начало" });
  s.addText("Бизнес-радар", { placeholder: "title" });
  s.addText("Новости, переведённые в рубли и действия для клиента Альфа-Бизнеса", { placeholder: "body" });
  s.addText("Альфа-Хакатон 2026 · трек КИБ «Аналитика новостей для бизнес-решений»",
    { x: 0.7, y: 6.4, w: 7, h: 0.4, fontSize: 14, color: "B9B9C2", margin: 0, isTextBox: true });
  s.addImage({ path: path.join(IMG, "00_globe.png"), x: 7.6, y: 1.45, w: 5.3, h: 3.31, objectName: "Глобус сигналов" });
  s.addText(STAND, { x: 7.6, y: 4.95, w: 5.3, h: 0.4, fontSize: 14, color: HEX.red, margin: 0, isTextBox: true,
    hyperlink: { url: STAND } });
  s.addNotes("Бизнес-радар: сервис внутри интернет-банка Альфа-Бизнеса. Показывает не ленту, а что событие значит для конкретной компании.");

  // ---------- 2. Проблема
  pres.addSection({ title: "Задача и решение" });
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Задача и решение" });
  s.addText("Клиенту нужна не лента новостей, а ответ «что это значит для меня»", { placeholder: "title" });
  const pains = [
    [ic.news, "Новостей слишком много", "Сотни сообщений в день от ЦБ, агентств и СМИ. У финдиректора нет времени их фильтровать."],
    [ic.link, "Неясно, касается ли меня", "Связь с отраслью, кредитом, ВЭД и контрагентами каждый раз приходится восстанавливать вручную."],
    [ic.rub, "Нет цифр и действий", "Непонятно, сколько событие стоит в рублях и что делать. Аналитика банка лежит в PDF и до клиента не доходит."],
  ];
  pains.forEach(([img, h, t], i) => {
    const x = 0.6 + i * 4.1;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.8, w: 3.8, h: 4.4, fill: { color: HEX.light }, line: { color: HEX.light }, rectRadius: 0.12,
      objectName: `Боль ${i + 1}` });
    s.addShape(pres.shapes.OVAL, { x: x + 0.4, y: 2.2, w: 0.9, h: 0.9, fill: { color: HEX.white }, line: { color: HEX.white } });
    s.addImage({ data: img, x: x + 0.62, y: 2.42, w: 0.46, h: 0.46 });
    s.addText(h, { x: x + 0.4, y: 3.35, w: 3.0, h: 0.8, fontSize: 20, bold: true, color: C.text1, margin: 0, valign: "top", isTextBox: true });
    s.addText(t, { x: x + 0.4, y: 4.2, w: 3.0, h: 1.8, fontSize: 15, color: C.text2, margin: 0, valign: "top", isTextBox: true });
  });

  // ---------- 3. Решение
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Задача и решение" });
  s.addText("Событие объясняется через профиль конкретной компании", { placeholder: "title" });
  const flow = [
    ["Событие", "новость из 15 источников или своя ссылка"],
    ["Почему вам", "отрасль, продукты банка, ВЭД, контрагенты из графа платежей, регион"],
    ["Риск или возможность", "и важность по шкале 0–3"],
    ["Рубли за 12 месяцев", "три сценария; база — прогноз Альфа-Банка"],
    ["Что делать", "кто и к какому сроку; продукт банка рядом"],
  ];
  flow.forEach(([h, t], i) => {
    const x = 0.6 + i * 2.47;
    s.addShape(pres.shapes.CHEVRON, { x, y: 1.75, w: 2.45, h: 0.75, fill: { color: i === 3 ? HEX.red : HEX.ink }, line: { color: HEX.white, width: 1 },
      objectName: `Шаг ${i + 1}` });
    s.addText(h, { x: x + 0.05, y: 2.65, w: 2.25, h: 0.7, fontSize: 17, bold: true, color: C.text1, margin: 0, valign: "top", isTextBox: true });
    s.addText(t, { x: x + 0.05, y: 3.3, w: 2.2, h: 1.2, fontSize: 14, color: C.text2, margin: 0, valign: "top", isTextBox: true });
  });
  const kinds = [["Факт", HEX.blue], ["Позиция Альфа-Банка", HEX.red], ["Вывод сервиса", HEX.violet]];
  s.addText("Каждое утверждение помечено и ведёт на первоисточник:", { x: 0.6, y: 4.85, w: 6.5, h: 0.4, fontSize: 15, color: C.text2, margin: 0,
    isTextBox: true });
  kinds.forEach(([t, col], i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6 + i * 2.75, y: 5.4, w: 2.55, h: 0.6, fill: { color: col }, line: { color: col },
      rectRadius: 0.3 });
    s.addText(t, { x: 0.6 + i * 2.75, y: 5.4, w: 2.55, h: 0.6, fontSize: 15, bold: true, color: C.background1, align: "center", valign: "middle",
      margin: 0, isTextBox: true });
  });
  s.addText("Профили 85 тыс. компаний и граф платежей из данных кейса · 5 ролей пользователя · 77 тезисов и 73 прогноза из 9 материалов банка",
    { x: 0.6, y: 6.25, w: 12, h: 0.5, fontSize: 14, color: C.accent5, margin: 0, isTextBox: true });

  // ---------- 4. Демо
  pres.addSection({ title: "Продукт" });
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Продукт" });
  s.addText("Демо: стенд работает на живых моделях и живом потоке новостей", { placeholder: "title" });
  s.addImage({ path: path.join(IMG, "03_radar_insight.png"), x: 0.6, y: 1.55, w: 8.0, h: 5.0, objectName: "Лента и разбор" });
  const demo = [
    ["Стенд", STAND],
    ["Вход", "любая демо-учётка на экране входа, пароль alfa2026"],
    ["Тур для жюри", "9 шагов, сервис сам откроет нужные экраны"],
    ["Проверить новость", "вставьте любую свежую новость — разбор с нуля"],
    ["Без интернета", "docker compose up: ответы моделей из снапшота, ключ не нужен"],
  ];
  demo.forEach(([h, t], i) => {
    s.addText([{ text: h, options: { bold: true, color: HEX.ink, breakLine: true } }, { text: t, options: { color: HEX.ink2 } }],
      { x: 8.95, y: 1.55 + i * 1.0, w: 3.8, h: 0.95, fontSize: 14, margin: 0, valign: "top", isTextBox: true });
  });

  // ---------- 5. Агент
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Продукт" });
  s.addText("Агент собран командой: план, инструменты, черновик, критик, правка", { placeholder: "title" });
  const steps = [["План", "Qwen"], ["Инструменты", "правила, allowlist"], ["Черновик", "DeepSeek"], ["Критик", "GLM — другая модель"],
    ["Правка", "только по делу"], ["Очистка", "без тех. id"]];
  steps.forEach(([h, m], i) => {
    const x = 0.6 + i * 2.05;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.7, w: 1.85, h: 1.25, fill: { color: i === 3 ? HEX.red : HEX.light },
      line: { color: i === 3 ? HEX.red : HEX.light }, rectRadius: 0.1 });
    s.addText([{ text: h, options: { bold: true, fontSize: 17, breakLine: true } }, { text: m, options: { fontSize: 13 } }],
      { x: x + 0.1, y: 1.75, w: 1.65, h: 1.15, color: i === 3 ? HEX.white : HEX.ink, align: "center", valign: "middle", margin: 0, isTextBox: true });
  });
  const stats = [["0,2 с · ~50 с", "готовый разбор · новый с нуля (замер стенда)"], ["≤ 4 ₽ · 60 с", "бюджет агента на один разбор"], ["100%", "разборов проходят самопроверку"]];
  stats.forEach(([n, t], i) => {
    s.addText(n, { x: 0.6 + i * 4.1, y: 3.45, w: 3.9, h: 0.9, fontSize: 40, bold: true, color: HEX.red, margin: 0, isTextBox: true });
    s.addText(t, { x: 0.6 + i * 4.1, y: 4.35, w: 3.9, h: 0.45, fontSize: 15, color: C.text2, margin: 0, isTextBox: true });
  });
  s.addText([
    { text: "Деньги считает калькулятор, а не модель. ", options: { bold: true } },
    { text: "Если эффект нельзя оценить честно, сервис пишет «заметного влияния не ожидаем».", options: { breakLine: true } },
    { text: "Каждый шаг пишется в трассировку: ", options: { bold: true } },
    { text: "вход, выход, модель, токены, стоимость — вкладка «Как получен вывод».", options: { breakLine: true } },
    { text: "Модели недоступны или бюджет исчерпан — ", options: { bold: true } },
    { text: "сервис продолжает работать на правилах." },
  ], { x: 0.6, y: 5.1, w: 12.1, h: 1.6, fontSize: 15, color: HEX.ink2, margin: 0, valign: "top", paraSpaceAfter: 6, isTextBox: true });

  // ---------- 6. Качество
  pres.addSection({ title: "Доказательства" });
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Доказательства" });
  s.addText("Качество в цифрах: метрики пересчитываются в CI на каждый PR", { placeholder: "title" });
  const rel = base.relevance || {};
  const cls = base.classification || {};
  const q = [
    [num(rel.p3 ?? 0.958), "P@3", `в топ-3 ленты только релевантное (${rel.pairs ?? 336} пар)`],
    [num(base.retrieval_recall5 ?? 0.975), "recall@5", "позиция банка находится, а не выдумывается"],
    [num(rel.critical_recall10 ?? 0.939), "критичное в топ-10", "важные события не теряются"],
    [num(cls.f1 ?? 0.835), "F1", "классификация новостей по факторам"],
  ];
  q.forEach(([n, m, t], i) => {
    const x = 0.6 + i * 3.1;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.7, w: 2.85, h: 2.6, fill: { color: HEX.light }, line: { color: HEX.light }, rectRadius: 0.1 });
    s.addText(n, { x: x + 0.25, y: 1.85, w: 2.4, h: 1.0, fontSize: 44, bold: true, color: HEX.red, margin: 0, isTextBox: true });
    s.addText(m, { x: x + 0.25, y: 2.85, w: 2.4, h: 0.4, fontSize: 16, bold: true, color: C.text1, margin: 0, isTextBox: true });
    s.addText(t, { x: x + 0.25, y: 3.25, w: 2.4, h: 0.95, fontSize: 14, color: C.text2, margin: 0, valign: "top", isTextBox: true });
  });
  const jo = judges && judges.agreement && judges.agreement.overall;
  const judgeLine = jo ? `Независимые LLM-судьи на ${judges.agreement.n} разборах (из 5): GLM ${num(jo.glm, 2)}, Kimi ${num(jo.kimi, 2)}; совпадение ±1 балл — ${within1(judges)} по осям; цель 4,2 пока не достигнута.`
    : "Независимые LLM-судьи: см. docs/llm/judges.md.";
  s.addText([
    { text: judgeLine, options: { breakLine: true } },
    { text: "Регрессия любой метрики больше чем на 0,05 блокирует слияние PR (quality gate).", options: { breakLine: true } },
    { text: "Ручная проверка 20 разборов по чек-листу и 10 эталонных пар «новость — знак эффекта»: 10 из 10." },
  ], { x: 0.6, y: 4.7, w: 12.1, h: 1.7, fontSize: 15, color: HEX.ink2, margin: 0, valign: "top", paraSpaceAfter: 6, isTextBox: true });

  // ---------- 7. Безопасность
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Доказательства" });
  s.addText("Безопасность проверена атаками: офлайн и на живых моделях", { placeholder: "title" });
  const redOk = red ? red.results.filter((r) => r.passed).length : null;
  const redN = red ? red.results.length : null;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y: 1.7, w: 4.6, h: 4.9, fill: { color: HEX.ink }, line: { color: HEX.ink }, rectRadius: 0.12 });
  s.addText("43 / 43", { x: 0.95, y: 1.95, w: 4.0, h: 1.0, fontSize: 48, bold: true, color: HEX.white, margin: 0, isTextBox: true });
  s.addText("офлайн-атаки в CI: инъекции, чат, чужие данные, SSRF", { x: 0.95, y: 2.95, w: 4.0, h: 0.8, fontSize: 15, color: "C9C9D1",
    margin: 0, valign: "top", isTextBox: true });
  s.addText(red ? `${redOk} / ${redN}` : "10 атак", { x: 0.95, y: 3.95, w: 4.0, h: 1.0, fontSize: 48, bold: true, color: HEX.red, margin: 0,
    isTextBox: true });
  s.addText("живой red-team через реальные модели: docs/llm/redteam_live.md", { x: 0.95, y: 4.95, w: 4.0, h: 0.9, fontSize: 15, color: "C9C9D1",
    margin: 0, valign: "top", isTextBox: true });
  const sec = [
    [ic.bug, "Prompt injection в новостях", "очистка текста, флаги атак, критик; атака не попадает в выводы"],
    [ic.lock, "Чужие данные", "пользователь видит только свои компании; каждый доступ — в журнале"],
    [ic.globe, "Загрузка по ссылке", "только публичные адреса: внутренняя сеть и file:// закрыты (SSRF)"],
    [ic.key, "Ключ к моделям", "только в GitHub Secrets; не в коде, не в логах, не в браузере"],
    [ic.shield, "Веб", "CSP, HttpOnly-сессии, лимиты частоты запросов по пользователю и IP"],
  ];
  sec.forEach(([img, h, t], i) => {
    const y = 1.7 + i * 1.0;
    s.addShape(pres.shapes.OVAL, { x: 5.6, y: y + 0.05, w: 0.7, h: 0.7, fill: { color: HEX.red }, line: { color: HEX.red } });
    s.addImage({ data: img, x: 5.77, y: y + 0.22, w: 0.36, h: 0.36 });
    s.addText([{ text: h, options: { bold: true, breakLine: true } }, { text: t, options: { color: HEX.ink2 } }],
      { x: 6.5, y, w: 6.2, h: 0.9, fontSize: 14, color: HEX.ink, margin: 0, valign: "top", isTextBox: true });
  });

  // ---------- 8. Экономика
  pres.addSection({ title: "Внедрение" });
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Внедрение" });
  s.addText("Экономика: около 4 ₽ на клиента в месяц", { placeholder: "title" });
  s.addChart(pres.charts.BAR, [{ name: "₽ за вызов", labels: ["План", "Критик", "Черновик", "Правка"], values: [0.09, 0.5, 0.7, 1.01] }], {
    x: 0.6, y: 1.6, w: 6.2, h: 4.4, barDir: "bar", showTitle: true, title: "Стоимость шага агента, ₽ (факт по 139 разборам)",
    titleFontSize: 14, titleColor: HEX.ink, titleFontFace: "+mn-lt", chartColors: [HEX.red], showValue: true, dataLabelPosition: "outEnd",
    dataLabelFontSize: 12, dataLabelColor: HEX.ink, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: "0.00",
    catAxisLabelColor: HEX.ink2, catAxisLabelFontSize: 13, catAxisLabelFontFace: "+mn-lt", valAxisHidden: true,
    valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false,
  });
  s.addText("1,79 ₽ — полный разбор в среднем (медиана 1,41 ₽)", { x: 0.6, y: 6.15, w: 6.2, h: 0.4, fontSize: 14, color: C.text2, margin: 0,
    isTextBox: true });
  const eco = [["10 тыс. клиентов", "≈ 41 тыс. ₽", "≈ 4,1 ₽"], ["100 тыс.", "≈ 386 тыс. ₽", "≈ 3,9 ₽"], ["1 млн", "≈ 3,8 млн ₽", "≈ 3,8 ₽"]];
  const hdr = (t) => ({ text: t, options: { bold: true, color: HEX.white, fill: { color: HEX.ink } } });
  s.addTable([[hdr("Клиентов"), hdr("Модели в месяц"), hdr("На клиента")], ...eco.map((r) => r.map((t) => ({ text: t })))], {
    x: 7.2, y: 1.7, w: 5.5, colW: [2.0, 1.9, 1.6], fontSize: 14, color: HEX.ink, border: { type: "solid", pt: 0.75, color: HEX.line },
    rowH: 0.5, margin: 0.08,
  });
  s.addText([
    { text: "Разбор события строится один раз на всех клиентов, лента ранжируется правилами (0 ₽), разбор кэшируется.", options: { breakLine: true } },
    { text: "При масштабе: разбор на сегмент и критик только для важных событий — 1–1,5 ₽ на клиента." },
  ], { x: 7.2, y: 4.0, w: 5.5, h: 2.0, fontSize: 14, color: HEX.ink2, margin: 0, valign: "top", paraSpaceAfter: 8, isTextBox: true });
  s.addText("Допущения: 25% клиентов активны, 8 разборов в месяц на активного — docs/ECONOMICS.md", { x: 7.2, y: 6.15, w: 5.5, h: 0.4,
    fontSize: 11, color: C.accent5, margin: 0, isTextBox: true });

  // ---------- 9. Пилот
  s = pres.addSlide({ masterName: "Контент", sectionTitle: "Внедрение" });
  s.addText("Пилот: 12 недель и решение по обращениям в банк", { placeholder: "title" });
  const phases = [["1–2", "Подготовка", "данные клиентов, IAM, юр. проверка"], ["3", "Внутренний запуск", "сотрудники и менеджеры"],
    ["4–7", "Волна 1", "1 000 компаний: ВЭД и кредит"], ["8–11", "Волна 2", "+1 000: стройка, торговля; push"], ["12", "Итоги", "решение о масштабе"]];
  s.addShape(pres.shapes.LINE, { x: 0.9, y: 2.15, w: 11.5, h: 0, line: { color: HEX.line, width: 2 } });
  phases.forEach(([wk, h, t], i) => {
    const x = 0.6 + i * 2.47;
    s.addShape(pres.shapes.OVAL, { x: x + 0.15, y: 1.85, w: 0.6, h: 0.6, fill: { color: i === 4 ? HEX.red : HEX.ink }, line: { color: HEX.white } });
    s.addText([{ text: `Недели ${wk}`, options: { fontSize: 12, color: HEX.muted, breakLine: true } },
      { text: h, options: { fontSize: 17, bold: true, color: HEX.ink, breakLine: true } }, { text: t, options: { fontSize: 13, color: HEX.ink2 } }],
    { x: x + 0.1, y: 2.65, w: 2.3, h: 1.5, margin: 0, valign: "top", isTextBox: true });
  });
  const mets = [["CTR виджета", "≥ 5%"], ["Доля 👍", "≥ 70%"], ["Скрытые карточки", "≤ 20%"], ["Обращения в банк: выше, чем в контрольной группе", "↑"]];
  mets.forEach(([h, v], i) => {
    const x = 0.6 + i * 3.1;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 4.6, w: 2.85, h: 1.4, fill: { color: i === 3 ? HEX.red : HEX.light },
      line: { color: i === 3 ? HEX.red : HEX.light }, rectRadius: 0.1 });
    s.addText([{ text: v, options: { fontSize: 26, bold: true, breakLine: true } }, { text: h, options: { fontSize: 14 } }],
      { x: x + 0.25, y: 4.7, w: 2.4, h: 1.2, color: i === 3 ? HEX.white : HEX.ink, margin: 0, valign: "middle", isTextBox: true });
  });
  s.addText("Контрольная группа без виджета — чтобы отличить эффект сервиса от сезонности. Все метрики уже собираются: экран «Качество и бюджет».",
    { x: 0.6, y: 6.2, w: 12.1, h: 0.5, fontSize: 13, color: C.text2, margin: 0, isTextBox: true });

  // ---------- 10. Команда
  s = pres.addSlide({ masterName: "Обложка", sectionTitle: "Внедрение" });
  s.addText("Команда", { placeholder: "title" });
  s.addText(TEAM, { placeholder: "body" });
  s.addText([
    { text: "Репозиторий: ", options: { bold: true } }, { text: REPO, options: { hyperlink: { url: "https://" + REPO }, breakLine: true } },
    { text: "Стенд: ", options: { bold: true } }, { text: STAND, options: { hyperlink: { url: STAND }, breakLine: true } },
    { text: "Пароль демо-учёток: ", options: { bold: true } }, { text: "alfa2026", options: { breakLine: true } },
    { text: "Документы: ", options: { bold: true } }, { text: "README · ARCHITECTURE · QUALITY · SECURITY · ECONOMICS · PILOT" },
  ], { x: 7.4, y: 2.2, w: 5.4, h: 2.6, fontSize: 15, color: "E6E6EA", margin: 0, valign: "top", paraSpaceAfter: 8, isTextBox: true });
  s.addText("Спасибо!", { x: 0.7, y: 6.1, w: 6, h: 0.7, fontSize: 28, bold: true, color: HEX.red, margin: 0, isTextBox: true });

  const file = path.join(OUT, "business-radar.pptx");
  await pres.writeFile({ fileName: file });
  if (process.env.APPLY_THEME) {
    const { applyTheme } = require(process.env.APPLY_THEME);
    await applyTheme(file, THEME);
  }
  console.log("Готово:", file);
}

main().catch((e) => { console.error(e); process.exit(1); });
