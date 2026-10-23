# Запуск: python tools/demo_capture.py <URL> <папка> [--video] [--smoke]   (нужен playwright с chromium)
"""Скриншоты экранов для README и видео-тур по стенду (около 3 минут).

Экраны снимаются в тех же именах, что в docs/img: вход, главная с глобусом, лента и разбор, сценарии и «что если»,
чат, трассировка, дайджест, «Качество и бюджет», тёмная тема, телефон. Каждый шаг в своём try: если один экран
не открылся, остальные всё равно снимаются, а в отчёте видно, что именно сломалось.

--smoke: проверка полного сценария без ключа (чистый клон → docker compose up → вход → разбор → чат → дайджест).
Код возврата 1, если хотя бы один шаг сценария не прошёл.
--video: дополнительно записать видео-тур (webm), его в mp4/gif переводит workflow через ffmpeg.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

URL, OUT = sys.argv[1].rstrip("/"), Path(sys.argv[2])
VIDEO, SMOKE = "--video" in sys.argv, "--smoke" in sys.argv
OUT.mkdir(parents=True, exist_ok=True)
report: dict[str, str] = {}
GHA = bool(os.environ.get("GITHUB_ACTIONS"))


async def step(name, coro):
    t0 = time.monotonic()
    try:
        await coro
        report[name] = f"ok ({time.monotonic() - t0:.1f} с)"
    except Exception as e:  # шаг не прошёл — фиксируем и идём дальше
        report[name] = f"ОШИБКА: {type(e).__name__}: {str(e)[:160]}"
    print(f"{name:28s} {report[name]}", flush=True)
    if GHA and report[name].startswith("ОШИБКА"):  # аннотация видна на странице запуска и в API без доступа к логам
        print(f"::error title=Съёмка: {name}::{report[name]}", flush=True)


async def login(pg, idx=0):
    await pg.goto(URL)
    await pg.wait_for_selector(".demo-acc", timeout=60000)
    await asyncio.sleep(1.5)
    await pg.click(f".demo-acc >> nth={idx}")
    try:
        await pg.wait_for_selector("#onboard[open]", timeout=8000)
        await asyncio.sleep(1.5)
        await pg.click("#obSkip")
    except Exception:
        pass
    await pg.wait_for_selector("#widgetList .w-item", timeout=60000)
    await asyncio.sleep(2)


async def wait_js(pg, expr: str, timeout: float = 60):
    """Ожидание условия опросом через evaluate: CSP стенда запрещает eval, а ожидание функции-строкой его использует."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if await pg.evaluate(f"() => !!({expr})"):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"не дождались: {expr[:80]}")


async def warm_first_insight(pg):
    """Заранее строим разбор первого события ленты через API (вживую — до двух минут), дальше экраны берут его из кэша."""
    await pg.evaluate("show('radar')")
    await pg.wait_for_selector(".feed-item", timeout=30000)
    ev = await pg.evaluate("() => document.querySelector('.feed-item').dataset.ev")
    status = await pg.evaluate("""async (ev) => (await fetch(`/api/insights/${ev}?company_id=${encodeURIComponent(state.company)}`
        + `&role=${encodeURIComponent(state.role || '')}`)).status""", ev)
    if status != 200:
        raise RuntimeError(f"разбор не построился: HTTP {status}")


async def open_first_insight(pg):
    await pg.evaluate("show('radar')")
    await pg.wait_for_selector(".feed-item", timeout=30000)
    await asyncio.sleep(1)
    await pg.click(".feed-item >> nth=0")
    # полный разбор: в replay приходит из снапшота сразу, вживую — до минуты
    # вживую разбор может строиться до двух минут (p95 ≈ 100 с), из кэша — мгновенно
    await pg.wait_for_selector("#insightPanel .tabs", timeout=180000)
    await pg.wait_for_selector("#insightPanel .effect-block", timeout=180000)
    await asyncio.sleep(1.5)


async def shots(b):
    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light", locale="ru-RU")
    pg = await ctx.new_page()

    async def s_login():
        await pg.goto(URL)
        await pg.wait_for_selector(".demo-acc", timeout=60000)
        await asyncio.sleep(3)  # глобус на обложке успевает отрисоваться
        await pg.screenshot(path=OUT / "01_login.png")

    async def s_home():
        await login(pg)
        await asyncio.sleep(2)
        await pg.screenshot(path=OUT / "00_globe.png")
        await pg.locator("#radarWidget").scroll_into_view_if_needed()
        await asyncio.sleep(1)
        await pg.screenshot(path=OUT / "02_home.png")

    async def s_insight():
        await warm_first_insight(pg)
        await open_first_insight(pg)
        await pg.screenshot(path=OUT / "03_radar_insight.png")

    async def s_scen():
        await pg.locator("#insightPanel .effect-block").scroll_into_view_if_needed()
        if await pg.locator("#whatIf").count():
            await pg.evaluate("document.querySelector('#whatIf').open = true")
            await asyncio.sleep(2)
        await pg.screenshot(path=OUT / "04_scenarios_whatif.png")

    async def s_chat():
        await pg.locator("#chatInput").scroll_into_view_if_needed()
        await pg.click(".chat .quick button >> text=Что говорит банк?")
        await wait_js(pg, "document.querySelector('#chatLog') && document.querySelector('#chatLog').innerText.length > 80", 90)
        await asyncio.sleep(4)  # ответ дописывается потоком
        await pg.locator("#chatLog").scroll_into_view_if_needed()
        await pg.screenshot(path=OUT / "05_chat.png")

    async def s_trace():
        await pg.click("#insightPanel .tab[data-tab=trace]")
        await asyncio.sleep(2.5)
        await pg.locator("#insightPanel .tabs").scroll_into_view_if_needed()
        await pg.screenshot(path=OUT / "06_trace.png")

    async def s_digest():
        await pg.evaluate("show('digest')")
        await wait_js(pg, "document.querySelector('#digestBox').innerText.length > 100", 60)
        await asyncio.sleep(2)
        await pg.screenshot(path=OUT / "07_digest.png")

    async def s_quality():
        await pg.evaluate("show('metrics')")
        await wait_js(pg, "document.querySelector('#metricsBox').innerText.length > 100", 60)
        await asyncio.sleep(2)
        await pg.screenshot(path=OUT / "08_quality.png")

    for name, fn in [("вход", s_login), ("главная", s_home), ("лента и разбор", s_insight), ("сценарии", s_scen),
                     ("чат", s_chat), ("трассировка", s_trace), ("дайджест", s_digest), ("качество и бюджет", s_quality)]:
        await step(name, fn())
    await ctx.close()

    if SMOKE:
        return
    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark", locale="ru-RU")
    pg = await ctx.new_page()

    async def s_dark():
        await login(pg)
        await pg.evaluate("applyTheme('dark')")
        await asyncio.sleep(1.5)
        await pg.screenshot(path=OUT / "09_home_dark.png")

    await step("тёмная тема", s_dark())
    await ctx.close()

    ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                              color_scheme="light", locale="ru-RU")
    pg = await ctx.new_page()

    async def s_mobile():
        await login(pg)
        await pg.evaluate("show('radar')")
        await pg.wait_for_selector(".feed-item", timeout=30000)
        await asyncio.sleep(2)
        await pg.screenshot(path=OUT / "10_mobile.png")

    await step("телефон", s_mobile())
    await ctx.close()


async def video(b):
    vdir = OUT / "video"
    ctx = await b.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light", locale="ru-RU",
                              record_video_dir=str(vdir), record_video_size={"width": 1280, "height": 800})
    pg = await ctx.new_page()

    async def tour():
        await login(pg)
        await asyncio.sleep(3)
        await pg.click("#tourBtn")
        await pg.wait_for_selector("#tour.on .tour-pop", timeout=15000)
        for _ in range(9):  # 9 шагов тура; шаги с разбором ждут его загрузки сами
            await asyncio.sleep(11)
            await pg.click("#tour [data-t=next]")
        await asyncio.sleep(2)

    async def after_tour():
        await open_first_insight(pg)
        await pg.locator("#chatInput").scroll_into_view_if_needed()
        await asyncio.sleep(1)
        await pg.fill("#chatInput", "Покажи разрез по контрагентам")
        await pg.click("#chatSend")
        await asyncio.sleep(25)  # ответ модели приходит потоком
        await pg.click("#insightPanel .tab[data-tab=trace]")
        await pg.locator("#insightPanel .tabs").scroll_into_view_if_needed()
        await asyncio.sleep(8)
        await pg.evaluate("show('metrics')")
        await asyncio.sleep(10)

    await step("видео: тур", tour())
    await step("видео: чат и трассировка", after_tour())
    path = await pg.video.path()
    await ctx.close()
    Path(path).rename(vdir / "tour.webm")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        await shots(b)
        if VIDEO:
            await video(b)
        await b.close()
    (OUT / "capture_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [k for k, v in report.items() if v.startswith("ОШИБКА")]
    print("Итог:", "все шаги прошли" if not failed else f"не прошли: {', '.join(failed)}")
    if GHA:
        print("::notice title=Съёмка::" + "; ".join(f"{k}: {v.split(' ')[0]}" for k, v in report.items()))
    if SMOKE and failed:
        sys.exit(1)


asyncio.run(main())
