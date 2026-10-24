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
import traceback
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


# какие элементы вылезают за правый край окна (для сообщения об ошибке)
WIDE_JS = """() => [...document.querySelectorAll('body *')].filter((e) => e.offsetParent && e.getBoundingClientRect().right > innerWidth + 1)
  .map((e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\\s+/).join('.') : ''))
  .slice(-6).join(', ')"""


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
    await pg.wait_for_selector("#detailToggle", timeout=180000)
    if not await pg.locator("#insightPanel .tabs").count():  # подробный разбор свёрнут за кнопкой
        await pg.click("#detailToggle")
    await pg.wait_for_selector("#insightPanel .tabs", timeout=30000)
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
        # первый экран: тёмный блок радара целиком и «Дела по радару» видны без прокрутки (ноутбук 1440×900)
        strip = await pg.evaluate("() => { const r = document.querySelector('#homeStrip').getBoundingClientRect(); return Math.round(r.top); }")
        if strip > 900 - 60:
            raise AssertionError(f"«Дела по радару» ниже первого экрана: top={strip} px")
        await pg.wait_for_selector("#focusCard .focus-main", timeout=30000)
        # «Дела по радару» и виджет ленты под тёмным блоком с глобусом
        await pg.evaluate("document.querySelector('#homeStrip').scrollIntoView({block: 'start'}); scrollBy(0, -80)")
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
        if await pg.locator("#insightPanel .bv-card").count():
            await pg.evaluate("document.querySelector('#insightPanel .bv-card').scrollIntoView({block: 'center'})")
            await asyncio.sleep(0.8)
            await pg.screenshot(path=OUT / "17_bank_view.png")

    async def s_chat():
        await pg.locator("#chatInput").scroll_into_view_if_needed()
        await pg.evaluate("""() => { const bs = [...document.querySelectorAll('.chat .quick button')];
            (bs.find((b) => /рубл/.test(b.textContent)) || bs.find((b) => /банк/i.test(b.textContent)) || bs[0]).click(); }""")
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

    async def s_tasks():
        await pg.click("#insightPanel .tab[data-tab=analysis]")
        await pg.wait_for_selector("#insightPanel [data-task]", timeout=20000)
        await pg.click("#insightPanel [data-task] >> nth=0")
        await asyncio.sleep(1)
        await pg.click(".nav-item[data-view=tasks]")
        await pg.wait_for_selector("#tasksBox .kb-card", timeout=20000)
        # этап 7: карточка → «В работе», исполнитель — роль
        await pg.click("#tasksBox .kb-col[data-col=open] [data-tmove] >> nth=0")
        await pg.wait_for_selector("#tasksBox .kb-col[data-col=doing] .kb-card", timeout=10000)
        await pg.select_option("#tasksBox .kb-col[data-col=doing] [data-towner] >> nth=0", index=1)
        await asyncio.sleep(0.8)
        await pg.screenshot(path=OUT / "13_tasks.png")
    async def s_calendar():
        await pg.click(".nav-item[data-view=calendar]")
        await pg.wait_for_selector("#calendarBox .cal-item, #calendarBox .card", timeout=20000)
        await asyncio.sleep(0.5)
        await pg.screenshot(path=OUT / "14_calendar.png")

    async def s_forecasts():
        await pg.click(".nav-item[data-view=forecasts]")
        await pg.wait_for_selector("#forecastsBox .fc-card", timeout=20000)
        # карточки не вылезают за края: ширина содержимого не больше ширины карточки
        over = await pg.evaluate("() => [...document.querySelectorAll('#forecastsBox .fc-card')].filter((c) => c.scrollWidth > c.clientWidth + 1).length")
        if over:
            raise AssertionError(f"текст вылезает из карточек прогнозов: {over}")
        await asyncio.sleep(0.5)
        await pg.screenshot(path=OUT / "15_forecasts.png")

    async def s_bank():
        for key, shot in (("accounts", None), ("credits", "11_bank_credits.png"), ("ved", None), ("salary", None)):
            await pg.click(f".nav-item[data-bank={key}]")
            await pg.wait_for_selector("#bankBox .bk-hero", timeout=30000)
            await asyncio.sleep(1)
            if shot:
                await pg.screenshot(path=OUT / shot)

    async def s_bell():
        await pg.click("#bellBtn")
        await pg.wait_for_selector("#bellPop:not([hidden])", timeout=10000)
        await asyncio.sleep(0.5)
        await pg.screenshot(path=OUT / "12_notifications.png")
        await pg.keyboard.press("Escape")

    async def s_tour():
        # тур по сервису: каждый шаг находит свой элемент на экране (иначе подсветка «повисает» на пустом месте)
        await pg.click(".nav-item[data-view=home]")
        await pg.click("#tourBtn")
        await pg.wait_for_selector("#tour.on .tour-pop", timeout=15000)
        steps = await pg.evaluate("TOUR.length")
        for k in range(steps):
            sel = await pg.evaluate(f"TOUR[{k}].sel")
            await wait_js(pg, f"document.querySelector({json.dumps(sel)})", 60)
            await asyncio.sleep(0.6)
            if k < steps - 1:
                await pg.click("#tour [data-t=next]")
        await pg.keyboard.press("Escape")
        await asyncio.sleep(0.5)

    async def s_widths():
        # оболочка на ширинах ноутбука: шапка в одну строку, у страницы нет горизонтальной прокрутки
        await pg.click(".nav-item[data-view=home]")
        for w in (1024, 1280):
            await pg.set_viewport_size({"width": w, "height": 800})
            await asyncio.sleep(1)
            top = await pg.evaluate("() => document.querySelector('.topbar').offsetHeight")
            wide = await pg.evaluate("() => document.documentElement.scrollWidth - innerWidth")
            await pg.screenshot(path=OUT / f"16_width_{w}.png")
            if top > 84 or wide > 1:
                who = await pg.evaluate(WIDE_JS)
                raise AssertionError(f"ширина {w}: высота шапки {top} px, лишняя ширина {wide} px; шире экрана: {who}")
        await pg.set_viewport_size({"width": 1440, "height": 900})

    async def s_stage5():
        # этап 5: поиск Ctrl+K, настройка главной, «всё прочитано», онбординг в 3 шага, хлебные крошки
        await pg.click(".nav-item[data-view=home]")
        await pg.keyboard.press("Control+k")
        await pg.wait_for_selector("#cmdk[open] .cmd-it", timeout=10000)
        await pg.keyboard.type("ставк")
        await asyncio.sleep(1.5)
        await pg.screenshot(path=OUT / "18_search.png")
        await pg.keyboard.press("Escape")
        await wait_js(pg, "!document.querySelector('#cmdk[open]')", 5)
        await pg.click("#homeSetupBtn")
        await pg.wait_for_selector("#actionDialog[open] .hs-setup", timeout=10000)
        await pg.screenshot(path=OUT / "19_home_setup.png")
        await pg.keyboard.press("Escape")
        await pg.evaluate("show('radar'); markAllRead(); document.querySelector('#fUnread').click()")
        await pg.wait_for_selector("#feedList .e-state", timeout=10000)
        await pg.screenshot(path=OUT / "20_all_read.png")
        await pg.evaluate("document.querySelector('#fUnread').click()")
        await pg.evaluate("onboardSteps()")
        await pg.wait_for_selector("#onboard[open] [data-ob='1']", timeout=10000)
        await pg.click("#onboard [data-ob='1']")
        await pg.wait_for_selector("#onboard .ob-roles", timeout=10000)
        await asyncio.sleep(0.5)
        await pg.screenshot(path=OUT / "21_onboard_role.png")
        await pg.click("#onboard [data-ob='2']")
        await pg.click("#onboard [data-ob='skip']")
        if not await pg.locator("#insightPanel .crumbs").count():
            raise AssertionError("в разборе нет хлебных крошек")

    async def s_stage7():
        # этап 7: заявка с расчётом и предпросмотром, статус обращения, история по событию
        await pg.evaluate("show('radar')")
        await pg.click("#insightPanel .tab[data-tab=analysis]")
        btn = "#insightPanel [data-act=application]" if await pg.locator("#insightPanel [data-act=application]").count() else "#insightPanel [data-act=manager]"
        await pg.click(btn + " >> nth=0")
        await pg.wait_for_selector("#actionDialog[open] .act-prev", timeout=10000)
        await asyncio.sleep(0.5)
        await pg.screenshot(path=OUT / "25_request_preview.png")
        await pg.click("#actSend")
        await wait_js(pg, "!document.querySelector('#actionDialog[open]')", 10)
        await pg.wait_for_selector("#evHistory .evh li", timeout=15000)
        await pg.click(".nav-item[data-view=tasks]")
        await pg.wait_for_selector("#tasksBox .req-card .req", timeout=15000)
        await pg.locator("#tasksBox .req-card").scroll_into_view_if_needed()
        await pg.screenshot(path=OUT / "26_requests.png")

    async def s_stage6():
        # этап 6: «для собственника», сравнение двух событий, вопрос по всей ленте с главной
        await pg.evaluate("show('radar')")
        if await pg.locator(".msg-tools [data-style=owner]").count():
            await pg.click(".msg-tools [data-style=owner] >> nth=0")
            await wait_js(pg, "document.querySelectorAll('#chatLog .msg.bot').length >= 2 && !document.querySelector('#chatLog .typing')", 90)
            await asyncio.sleep(1)
            await pg.locator("#chatLog").scroll_into_view_if_needed()
            await pg.screenshot(path=OUT / "22_chat_owner.png")
        await pg.evaluate("document.querySelector('.cmp').open = true")
        await pg.click("#cmpGo")
        await pg.wait_for_selector("#cmpOut .cmp-verdict", timeout=10000)
        await pg.locator(".cmp").scroll_into_view_if_needed()
        await pg.screenshot(path=OUT / "23_compare.png")
        await pg.click(".nav-item[data-view=home]")
        await pg.locator("#feedChat").scroll_into_view_if_needed()
        await pg.click("#feedChat [data-fq] >> nth=0")
        await wait_js(pg, "document.querySelector('#fcLog .msg.bot') && !document.querySelector('#fcLog .typing') && document.querySelector('#fcLog').innerText.length > 60", 90)
        await asyncio.sleep(1)
        await pg.screenshot(path=OUT / "24_feed_chat.png")

    for name, fn in [("вход", s_login), ("главная", s_home), ("лента и разбор", s_insight), ("сценарии", s_scen),
                     ("чат", s_chat), ("трассировка", s_trace), ("мои действия", s_tasks), ("календарь", s_calendar), ("прогнозы банка", s_forecasts), ("дайджест", s_digest), ("качество и бюджет", s_quality),
                     ("разделы банка", s_bank), ("уведомления", s_bell), ("тур по сервису", s_tour), ("ширины экрана", s_widths), ("этап 5: навигация", s_stage5), ("этап 6: чат", s_stage6), ("этап 7: действия", s_stage7)]:
        await step(name, fn())
    await ctx.close()

    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark", locale="ru-RU")
    pg = await ctx.new_page()

    async def s_dark():
        await login(pg)
        await pg.evaluate("applyTheme('dark')")
        await asyncio.sleep(1.5)
        await pg.screenshot(path=OUT / "09_home_dark.png")
        await pg.evaluate("document.querySelector('#homeStrip').scrollIntoView({block: 'start'}); scrollBy(0, -80)")
        await asyncio.sleep(0.8)
        await pg.screenshot(path=OUT / "09b_home_dark_strip.png")
        await open_first_insight(pg)
        await pg.screenshot(path=OUT / "09c_insight_dark.png")

    await step("тёмная тема", s_dark())
    await ctx.close()

    ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                              color_scheme="light", locale="ru-RU")
    pg = await ctx.new_page()

    async def s_mobile():
        # экран входа на телефоне: форма не шире экрана
        await pg.goto(URL)
        await pg.wait_for_selector(".demo-acc", timeout=60000)
        form = await pg.evaluate("() => { const r = document.querySelector('.login-form').getBoundingClientRect(); return Math.round(r.right - innerWidth); }")
        await pg.screenshot(path=OUT / "10d_mobile_login.png")
        if form > 1:
            raise AssertionError(f"форма входа шире экрана телефона на {form} px")
        await login(pg)
        await pg.evaluate("show('radar')")
        await pg.wait_for_selector(".feed-item", timeout=30000)
        await asyncio.sleep(2)
        await pg.screenshot(path=OUT / "10_mobile.png")
        wide = await pg.evaluate("() => document.documentElement.scrollWidth - innerWidth")
        await pg.click("#ctxBtn")
        await asyncio.sleep(0.5)
        await pg.screenshot(path=OUT / "10b_mobile_context.png")
        await pg.click("#ctxBtn")
        await pg.click("#bellBtn")
        await asyncio.sleep(0.5)
        bell = await pg.evaluate("() => { const r = document.querySelector('#bellPop').getBoundingClientRect(); return [Math.round(r.left), Math.round(innerWidth - r.right)]; }")
        await pg.screenshot(path=OUT / "10e_mobile_bell.png")
        if min(bell) < 0:
            raise AssertionError(f"окно уведомлений за краем экрана: {bell}")
        await pg.keyboard.press("Escape")
        await pg.click("#navMore")
        await asyncio.sleep(0.6)
        await pg.screenshot(path=OUT / "10c_mobile_menu.png")
        await pg.click(".nav-item[data-view=forecasts]")
        await pg.wait_for_selector("#view-forecasts.active", timeout=10000)
        if await pg.evaluate("document.body.classList.contains('nav-open')"):
            raise AssertionError("меню «Ещё» не закрылось после перехода в раздел")
        if wide > 1:
            raise AssertionError(f"на телефоне лишняя ширина {wide} px; шире экрана: {await pg.evaluate(WIDE_JS)}")

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
        steps = await pg.evaluate("TOUR.length")
        for _ in range(steps - 1):  # шаги тура; шаги с разбором ждут его загрузки сами
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
        # PW_CHANNEL=chrome — Google Chrome с образа раннера (без скачивания браузера)
        b = await p.chromium.launch(channel=os.environ.get("PW_CHANNEL") or None)
        for name, fn in [("скриншоты", shots)] + ([("видео", video)] if VIDEO else []):
            try:
                await fn(b)
            except Exception as e:  # сбой вне шага: причина — в аннотации запуска, остальное продолжает сниматься
                report[name] = f"ОШИБКА: {type(e).__name__}: {str(e)[:200]}"
                print(f"{name}: {report[name]}\n{traceback.format_exc()}", flush=True)
                if GHA:
                    print(f"::error title=Съёмка: {name}::{report[name]}", flush=True)
        await b.close()
    (OUT / "capture_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [k for k, v in report.items() if v.startswith("ОШИБКА")]
    print("Итог:", "все шаги прошли" if not failed else f"не прошли: {', '.join(failed)}")
    if GHA:
        print("::notice title=Съёмка::" + "; ".join(f"{k}: {v.split(' ')[0]}" for k, v in report.items()))
    if SMOKE and failed:
        sys.exit(1)


asyncio.run(main())
