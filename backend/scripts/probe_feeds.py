"""Проверка адресов RSS-кандидатов перед добавлением в config/sources.yaml (этап 8 и этап 3 плана улучшений).

Адрес не угадываем: каждый кандидат скачивается тем же адаптером, что и на сервере (app/news/sources.RSSSource),
и в отчёт попадает: отвечает ли, сколько новостей, дата самой свежей, доля новостей про экономику по словарю.
Запуск на раннере GitHub (из песочницы разработки сайты недоступны):
  python scripts/probe_feeds.py
Результат — аннотации запуска и docs/llm/feeds_probe.md.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import REPO_ROOT
from app.news.sources import RSSSource

CANDIDATES = [
    # этап 3 плана улучшений: отраслевые источники (стройка и недвижимость, ритейл, логистика, агро, авто, металлы, ТЭК)
    ("vedomosti_realty", "Ведомости — недвижимость", "media", "https://www.vedomosti.ru/rss/rubric/realty"),
    ("vedomosti_retail", "Ведомости — потребительский рынок", "media", "https://www.vedomosti.ru/rss/rubric/business/retail"),
    ("vedomosti_auto", "Ведомости — авто", "media", "https://www.vedomosti.ru/rss/rubric/auto"),
    ("vedomosti_industry", "Ведомости — промышленность", "media", "https://www.vedomosti.ru/rss/rubric/business/industry"),
    ("vedomosti_energy", "Ведомости — ТЭК", "media", "https://www.vedomosti.ru/rss/rubric/business/energy"),
    ("vedomosti_transport", "Ведомости — транспорт", "media", "https://www.vedomosti.ru/rss/rubric/business/transport"),
    ("vedomosti_agro", "Ведомости — агро", "media", "https://www.vedomosti.ru/rss/rubric/business/agriculture"),
    ("stroygaz", "Строительная газета", "media", "https://www.stroygaz.ru/rss/"),
    ("erzrf", "ЕРЗ.РФ — жилищное строительство", "media", "https://erzrf.ru/rss/news"),
    ("agroinvestor_news", "Агроинвестор", "media", "https://www.agroinvestor.ru/rss/news/"),
    ("new_retail", "New Retail", "media", "https://new-retail.ru/rss/"),
    ("retail_ru", "Retail.ru", "media", "https://www.retail.ru/rss/"),
    ("logirus", "Logirus — логистика", "media", "https://logirus.ru/rss/"),
    ("autostat", "Автостат", "media", "https://www.autostat.ru/rss/"),
    ("metalinfo", "Металлоснабжение и сбыт", "media", "https://www.metalinfo.ru/ru/news/rss"),
    ("neftegaz", "Neftegaz.RU", "media", "https://neftegaz.ru/rss/news/"),
    ("interfax_realty", "Интерфакс — недвижимость", "agency", "https://realty.interfax.ru/ru/rss/"),
    ("bfm", "BFM.ru — новости", "media", "https://www.bfm.ru/news.rss?type=news"),
]
ECON = re.compile(r"налог|ставк|кредит|банк|рубл|курс|инфляц|бюджет|экспорт|импорт|санкц|рынок|цен|закон|компани|бизнес|"
                  r"зарплат|ВВП|субсид|льгот|тариф|пошлин|маркировк|ипотек|лизинг|МСП|предпринимат", re.I)


def probe(cid: str, title: str, trust: str, url: str) -> dict:
    row = {"id": cid, "title": title, "trust": trust, "url": url}
    try:
        items = RSSSource({"id": cid, "title": title, "url": url}).fetch()
    except Exception as e:  # сетевые ошибки, 403/404, не XML — всё это «не подходит»
        row["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return row
    dates = sorted((i.published_at for i in items if i.published_at), reverse=True)
    row.update(n=len(items), latest=dates[0][:16] if dates else None,
               econ=round(sum(bool(ECON.search(i.title + " " + i.text)) for i in items) / len(items), 2) if items else 0,
               sample=items[0].title[:90] if items else "")
    age = None
    if dates:
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(dates[0]).astimezone(UTC)).days
        except ValueError:
            age = None
    row["fresh_days"] = age
    row["ok"] = bool(items) and age is not None and age <= 7
    return row


def main() -> None:
    rows = [probe(*c) for c in CANDIDATES]
    gha = bool(os.environ.get("GITHUB_ACTIONS"))
    L = ["# Проверка RSS-кандидатов", "", f"Прогон: {datetime.now(UTC).isoformat()[:16]} UTC", "",
         "| Источник | Адрес | Ответ | Новостей | Свежая | Доля про экономику | Пример |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        status = "✓" if r.get("ok") else ("ошибка" if r.get("error") else "старое/пусто")
        L.append(f"| {r['title']} | {r['url']} | {status} | {r.get('n', '—')} | {r.get('latest') or '—'} | "
                 f"{r.get('econ', '—')} | {(r.get('sample') or r.get('error') or '').replace('|', '/')} |")
        line = f"{r['id']} {status}: n={r.get('n')} latest={r.get('latest')} econ={r.get('econ')} {r.get('error', '')}"
        print(line)
        if gha:
            print(f"::notice title=RSS {r['id']}::{line}")
    out = REPO_ROOT / "docs" / "llm" / "feeds_probe.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
