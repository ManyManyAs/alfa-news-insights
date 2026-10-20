"""Этап 4: живой поток новостей — сущности, шум, дедупликация, сюжеты, источники, лента."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


def test_entities():
    from app.news.entities import extract_effective_dates, extract_industries, extract_regions, is_noise

    text = "Застройщики Новосибирска и Казани: программа вступает в силу с 1 декабря, вторая часть — с 01.03.2027."
    assert extract_regions(text) == ["Новосибирская область", "Республика Татарстан"]
    assert "41" in extract_industries(text)
    assert extract_effective_dates(text, "2026-10-15T10:00:00+03:00") == ["2026-12-01", "2027-03-01"]
    # «Московская биржа» — не регион
    assert extract_regions("Индекс Московской биржи вырос на 2%") == []
    assert is_noise("Хоккейный матч завершился победой хозяев")
    assert not is_noise("Банк России сохранил ключевую ставку")


def test_minhash_dedup():
    from app.news.dedup import BORDERLINE_AT, MERGE_AT, match_score, minhash, minhash_sim

    a = ("ЦБ сохранил ключевую ставку 14%", "Совет директоров Банка России сохранил ставку на уровне 14% годовых")
    b = ("Банк России оставил ставку без изменений", "Регулятор оставил ключевую ставку 14% годовых, сигнал стал жёстче")
    c = ("Продажи автомобилей упали", "Продажи новых легковых автомобилей снизились на 7%")
    assert minhash_sim(minhash(a[0] + a[1]), minhash(a[0] + a[1])) == 1.0
    reprint = ("Банк России сохранил ключевую ставку 14%", "Совет директоров Банка России сохранил ставку на уровне 14% годовых.")
    assert match_score(*a, None, None, *reprint, None, None) >= MERGE_AT  # перепечатка — склеиваем сразу
    assert match_score(*a, None, None, *b, None, None) >= BORDERLINE_AT  # пересказ — как минимум к арбитру
    assert match_score(*a, None, None, *c, None, None) < 0.1
    # вне окна по времени — не склеиваем даже одинаковые тексты
    assert match_score(*a, "2026-09-01T00:00:00", None, *a, "2026-09-20T00:00:00", None) == 0.0


def test_classify_gating_skips_llm_without_factors(monkeypatch):
    """Новость без бизнес-факторов из СМИ не отправляется в модель (экономия бюджета); официальная — отправляется."""
    from app.news import classify as cl
    from app.news.models import NewsItem

    calls = []

    class FakeLLM:
        enabled = True

        def complete_json(self, *a, **k):
            calls.append(a[0])
            raise cl.LLMUnavailable("нет")

    monkeypatch.setattr(cl, "get_llm", lambda: FakeLLM())
    item = NewsItem(id="x1", source="t", title="Певица выпустила альбом", text="Новый альбом вышел вчера.")
    assert cl.classify(item, "media").classifier == "rules" and not calls
    cl.classify(item, "official")
    assert calls == ["classify_news"]


def test_seed_pipeline_stories_and_noise():
    from app.news.ingest import list_events, sources_health
    from app.storage import db

    events = list_events(100)
    titles = [e.title for e in events]
    assert not any("хоккею" in t for t in titles), "шум не должен попадать в события"
    stories = db.query("SELECT * FROM stories")
    assert len(stories) >= 2
    rate = [s for s in stories if s["factor"] == "key_rate"]
    assert rate and len(json.loads(rate[0]["event_ids"])) >= 3  # ставка → ОФЗ → прогнозы аналитиков
    demo = next(s for s in sources_health()["sources"] if s["id"] == "demo_seed")
    assert demo["status"] == "ok" and demo["noise"] >= 1
    ev = next(e for e in events if "Новосибирская" in e.title)
    assert ev.classification.regions == ["Новосибирская область"] and "2027-01-01" in ev.classification.effective_dates
    assert ev.classification.trust == "demo"


def test_failing_source_is_reported(monkeypatch):
    from app.news import ingest
    from app.news.sources import Source

    class Broken(Source):
        def fetch(self):
            raise ConnectionError("timeout")

    monkeypatch.setattr(ingest, "enabled_sources", lambda only=None: [Broken({"id": "broken", "title": "Сломанный",
                                                                              "type": "rss", "trust": "media"})])
    stats = ingest.run_ingest()
    assert stats["errors"] and stats["new_events"] == 0
    row = ingest.db.query_one("SELECT failures, last_error FROM source_status WHERE id='broken'")
    assert row["failures"] == 1 and "timeout" in row["last_error"]


def test_atom_feed(monkeypatch):
    from app.news import sources

    atom = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Минфин разместил ОФЗ</title><link rel="alternate" href="https://example.org/a"/>
      <summary>Выручка составила 40 млрд рублей.</summary><updated>2026-10-20T10:00:00Z</updated></entry></feed>""".encode()

    class R:
        content = atom

        def raise_for_status(self):
            pass

    class C:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url):
            return R()

    monkeypatch.setattr(sources.httpx, "Client", C)
    items = sources.RSSSource({"id": "atom", "title": "Atom", "url": "https://example.org/feed"}).fetch()
    assert items[0].title == "Минфин разместил ОФЗ" and items[0].url == "https://example.org/a"
    assert items[0].published_at.startswith("2026-10-20T10:00:00")


def test_migration_adds_columns(tmp_path):
    """Старая БД без новых колонок обновляется на лету (на сервере БД живёт между деплоями)."""
    from app.storage import db

    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE events (id TEXT PRIMARY KEY, title TEXT, published_at TEXT, classification TEXT, news_ids TEXT)")
    conn.commit()
    conn.close()
    c = db._connect(p)
    cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
    assert "story_id" in cols
    c.close()


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_api_feed_story_and_sources(client):
    users = client.get("/api/demo-users").json()["users"]
    h = {"X-User-Id": users[0]["user_id"]}
    items = client.get(f"/api/feed?company_id={users[0]['primary_company']}", headers=h).json()["items"]
    assert items and all("trust" in i and "regions" in i and "story" in i for i in items)
    sids = [i["story"]["id"] for i in items if i["story"]]
    assert len(sids) == len(set(sids)), "сюжет должен быть свёрнут в одну карточку"
    from app.storage import db

    sid = db.query_one("SELECT id FROM stories LIMIT 1")["id"]
    st = client.get(f"/api/stories/{sid}").json()
    assert len(st["events"]) >= 2 and st["events"][0]["published_at"] <= st["events"][-1]["published_at"]
    health = client.get("/api/sources/health").json()
    assert {"schedule_minutes", "sources"} <= set(health) and any(s["trust"] == "official" for s in health["sources"])
