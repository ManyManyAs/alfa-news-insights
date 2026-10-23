// «Как мы проверяли»: независимые проверки качества и безопасности — с цифрами и ссылками на отчёты в репозитории.
/* global api, esc */
async function renderProof(box) {
  let p;
  try { p = await api("/api/proof"); } catch (e) { return; }
  const n = (x, d = 2) => (x == null ? "—" : Number(x).toFixed(d).replace(".", ","));
  const doc = (path, t) => `<a href="${esc(p.repo)}/blob/main/${path}" target="_blank" rel="noopener">${esc(t)}</a>`;
  const card = (big, title, text, link) => `<div class="pf-card"><b>${big}</b><span class="pf-t">${title}</span><span class="muted small">${text}</span><span class="small">${link}</span></div>`;
  box.innerHTML = `<div class="card proof"><div class="eyebrow">Как мы проверяли</div><h3>Цифрам можно не верить на слово — вот проверки</h3>
    <div class="pf-grid">
      ${card(n(p.offline.p3, 3), "P@3 ленты", "в топ-3 только релевантное — по ручной разметке 336 пар «профиль — новость»; пересчитывается в CI на каждый PR", doc("docs/QUALITY.md", "методика"))}
      ${card(n(p.offline.recall5, 3), "позиция банка находится", "recall@5: тезис банка находится, а не выдумывается", doc("docs/QUALITY.md", "подробно"))}
      ${card(`${n(p.judges.glm)} / ${n(p.judges.kimi)}`, "два независимых LLM-судьи", `GLM и Kimi на ${p.judges.n || "—"} разборах (из 5), автор разборов — третья модель; ${esc(p.judges.run_at)}`, doc("docs/llm/judges.md", "оценки и замечания"))}
      ${card(`${p.redteam.passed} / ${p.redteam.total}`, "живой red-team", `атаки через реальные модели: инъекции в новостях, подмена позиции банка, чужие данные, ключ; ${esc(p.redteam.run_at)}`, doc("docs/SECURITY.md", "что нашли и исправили"))}
      ${card("≈ 4 ₽", "на клиента в месяц", "по трассировкам 139 разборов; разбор события строится один раз на всех", doc("docs/ECONOMICS.md", "расчёт"))}
      ${card("12 недель", "план пилота", "сегменты, контрольная группа, метрики с порогами", doc("docs/PILOT.md", "план"))}
    </div></div>`;
}
