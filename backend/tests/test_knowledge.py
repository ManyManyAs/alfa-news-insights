"""База знаний банка: тезисы, прогнозы и их пересмотры, гибридный поиск, позиция банка в инсайте."""
import numpy as np
import pytest

from app.evaluation import retrieval_recall
from app.knowledge import bank as bank_mod
from app.knowledge import embeddings as emb_mod
from app.knowledge.bank import BankKnowledge, get_knowledge


def test_theses_cover_all_documents_and_have_types():
    kb = get_knowledge()
    assert len(kb.theses) >= 70
    assert {t["doc_id"] for t in kb.theses} == set(kb.documents)
    assert all(t["type"] in {"forecast", "assessment", "recommendation", "risk"} for t in kb.theses)
    assert all(t["stale"] for t in kb.theses if t["doc_id"] == "russian_steels_1q21")  # материал 2021 г.


def test_forecast_revision_is_tracked():
    f = get_knowledge().forecast("key_rate_end", "2026")
    assert f["value"] == 14.0 and f["revision"]["from"] == 13.5 and f["revision"]["direction"] == "up"
    assert "повысил прогноз с 13,5%" in f["text"] and f["ref"] == "bank:fc-key_rate_end-2026"
    assert any(r["indicator"] == "usd_rub" and r["period"] == "2027" for r in get_knowledge().revisions())


def test_retrieval_recall_bm25():
    rr = retrieval_recall(k=5)
    assert rr["mode"] == "bm25" and rr["recall"] >= 0.9, rr["misses"]


def test_hybrid_search_uses_dense_ranking(monkeypatch):
    class FakeEmbedder:
        """Детерминированный «эмбеддер»: dense-ранжирование в обратном порядке чанков — проверяем RRF-слияние."""
        def index(self, texts):
            return np.zeros((len(texts), 3))

        def rank(self, query, matrix):
            return list(range(len(matrix)))[::-1]

    bm25_top = [h["id"] for h in BankKnowledge().search("ключевая ставка", k=5)]
    monkeypatch.setattr(emb_mod, "get_embedder", lambda: FakeEmbedder())
    kb = BankKnowledge()
    hybrid = [h["id"] for h in kb.search("ключевая ставка", k=5)]
    dense_top = {c.id for c in kb.chunks[::-1][:5]}
    assert len(hybrid) == 5 and hybrid != bm25_top  # слияние рангов меняет выдачу
    assert set(hybrid) & (set(bm25_top) | dense_top)


def test_insight_bank_view_has_forecast(monkeypatch):
    from app.agent.orchestrator import run_agent
    from app.bootstrap import load_demo_users
    from app.data.profiles import get_profile
    from app.news.ingest import list_events

    u = load_demo_users()[0]
    p = get_profile(u["primary_company"])
    ev = next(e for e in list_events() if "ставк" in e.title.lower() and "ключев" in e.title.lower())
    ins = run_agent(ev, p, "cfo")
    refs = [s for c in ins.bank_view for s in c.sources]
    assert any(r.startswith("bank:fc-") for r in refs), refs
    assert any(s.type == "bank" and s.id.startswith("bank:fc-") for s in ins.sources)
    assert all(c.passed for c in ins.quality)


def test_bank_forecasts_endpoint():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        data = c.get("/api/bank/forecasts").json()
    assert data["theses_count"] >= 70 and data["revisions"] and data["rate_path"]["points"]


@pytest.fixture(autouse=True)
def _no_cache_leak():
    yield
    bank_mod.get_knowledge.cache_clear()


def test_russian_stemmer_matches_word_forms():
    from app.knowledge.stemmer import stem

    assert stem("Китаем") == stem("Китай")
    assert stem("юанях") == stem("юани")
    assert stem("ставку") == stem("ставки") == stem("ставка")
    assert stem("инфляции") == stem("инфляция")
