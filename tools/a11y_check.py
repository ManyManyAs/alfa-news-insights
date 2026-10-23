# Запуск: python tools/a11y_check.py http://localhost:8000 /tmp  (нужен playwright с chromium)
"""Проверка доступности без внешних пакетов: контраст текста (WCAG AA), имена кнопок/ссылок, подписи полей,
alt у изображений, lang, видимость фокуса. Экраны: вход, главная, лента + разбор, дайджест; светлая и тёмная тема."""
import asyncio, json, sys
from playwright.async_api import async_playwright

URL, OUT = sys.argv[1], sys.argv[2]
JS = r"""
() => {
  const lum = (r,g,b) => { const f = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); }; return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b); };
  const parse = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [p[0],p[1],p[2], p.length>3?p[3]:1]; };
  const bgOf = (el) => { let e = el; let acc = null;
    while (e && e.nodeType === 1) { const cs = getComputedStyle(e); if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = parse(cs.backgroundColor); if (c && c[3] > 0.05) { if (c[3] >= 0.95) return c; acc = acc || c; }
      e = e.parentElement; }
    const b = parse(getComputedStyle(document.body).backgroundColor); return b || [255,255,255,1]; };
  const issues = { contrast: [], names: [], labels: [], alt: [] };
  const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!own) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || +cs.opacity < 0.1) continue;
    const fg = parse(cs.color), bg = bgOf(el); if (!fg || !bg) continue;
    const a = fg[3]; const mix = [0,1,2].map((i) => fg[i]*a + bg[i]*(1-a));
    const L1 = lum(...mix), L2 = lum(bg[0],bg[1],bg[2]); const ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
    const size = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 700; const large = size >= 24 || (bold && size >= 18.66);
    const need = large ? 3 : 4.5;
    if (ratio < need) { const key = el.className + '|' + cs.color; if (seen.has(key)) continue; seen.add(key);
      issues.contrast.push({ sel: el.tagName.toLowerCase() + (el.id ? '#'+el.id : '') + (el.className && typeof el.className === 'string' ? '.'+el.className.split(' ').join('.') : ''), ratio: +ratio.toFixed(2), need, color: cs.color, text: el.textContent.trim().slice(0,40) }); }
  }
  for (const el of document.querySelectorAll('button, a[href], [role=button]')) {
    if (!el.offsetParent) continue;
    const name = (el.getAttribute('aria-label') || el.textContent || el.title || '').trim();
    if (!name) issues.names.push(el.outerHTML.slice(0, 120));
  }
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (!el.offsetParent || el.type === 'hidden') continue;
    const ok = el.getAttribute('aria-label') || el.closest('label') || (el.id && document.querySelector(`label[for="${el.id}"]`)) || el.title;
    if (!ok) issues.labels.push(el.outerHTML.slice(0, 120));
  }
  for (const el of document.querySelectorAll('img')) if (!el.hasAttribute('alt')) issues.alt.push(el.outerHTML.slice(0,100));
  issues.lang = document.documentElement.lang;
  return issues;
}
"""


async def focus_check(pg, n=25):
    bad = []
    await pg.keyboard.press("Tab")
    for _ in range(n):
        r = await pg.evaluate("""() => { const e = document.activeElement; if (!e || e === document.body) return null; const cs = getComputedStyle(e);
          const vis = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== 'none';
          return { el: e.tagName + '.' + (typeof e.className === 'string' ? e.className : ''), vis }; }""")
        if r and not r["vis"]:
            bad.append(r["el"])
        await pg.keyboard.press("Tab")
    return sorted(set(bad))


async def main():
    report = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for theme in ("light", "dark"):
            ctx = await b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=theme)
            pg = await ctx.new_page()
            await pg.goto(URL); await pg.wait_for_selector(".demo-acc")
            await asyncio.sleep(1)
            report[f"{theme}:login"] = await pg.evaluate(JS)
            report[f"{theme}:login:focus"] = await focus_check(pg, 12)
            await pg.click(".demo-acc >> nth=0")
            try:
                await pg.wait_for_selector("#onboard[open]", timeout=6000); await pg.click("#obSkip")
            except Exception:
                pass
            await pg.wait_for_selector("#widgetList .w-item"); await asyncio.sleep(1.5)
            report[f"{theme}:home"] = await pg.evaluate(JS)
            report[f"{theme}:home:focus"] = await focus_check(pg, 30)
            await pg.evaluate("show('radar')"); await asyncio.sleep(1)
            await pg.click(".feed-item >> nth=0"); await pg.wait_for_selector("#detailToggle"); await asyncio.sleep(1)
            await pg.click("#detailToggle"); await asyncio.sleep(1)
            report[f"{theme}:insight"] = await pg.evaluate(JS)
            await pg.evaluate("show('digest')"); await asyncio.sleep(2)
            report[f"{theme}:digest"] = await pg.evaluate(JS)
            await ctx.close()
        await b.close()
    json.dump(report, open(f"{OUT}/a11y.json", "w"), ensure_ascii=False, indent=1)
    for k, v in report.items():
        if isinstance(v, list):
            print(k, "без видимого фокуса:", v[:8]); continue
        print(f"== {k}: контраст {len(v['contrast'])}, имена {len(v['names'])}, подписи {len(v['labels'])}, alt {len(v['alt'])}, lang={v['lang']}")
        for c in v["contrast"][:12]:
            print("   ", c["ratio"], "<", c["need"], c["sel"][:60], "|", c["text"])
        for x in (v["names"] + v["labels"])[:6]:
            print("   !", x)

asyncio.run(main())
