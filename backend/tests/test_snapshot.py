"""Режим replay: ответы LLM из снапшота работают без ключа; промах → детерминированный режим."""
import json

import pytest

from app.config import get_settings
from app.llm.client import LLMClient, LLMUnavailable, cache_key
from app.storage import db
from app.storage.snapshot import export_snapshot, import_snapshot


@pytest.fixture
def replay(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "auto")
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    yield LLMClient()
    monkeypatch.setenv("LLM_MODE", "mock")
    get_settings.cache_clear()


def test_auto_without_key_is_replay(replay):
    assert replay.mode == "replay" and replay.enabled and not replay.live


def test_replay_hit_and_miss(replay):
    msgs = [{"role": "user", "content": "тестовый вопрос replay"}]
    model = replay.cfg["models"][replay.cfg["tasks"]["chat"]["model"]]["id"]
    payload = replay._payload(model, msgs, False, 600, 0.2)
    with db.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO llm_cache(key, task, model, response) VALUES (?,?,?,?)",
                     (cache_key(payload), "chat", model, json.dumps({"text": "ответ из снапшота"})))
    res = replay.complete("chat", msgs, max_tokens=600)
    assert res.cached and res.text == "ответ из снапшота"
    with pytest.raises(LLMUnavailable):
        replay.complete("chat", [{"role": "user", "content": "вопроса нет в снапшоте"}], max_tokens=600)


def test_snapshot_roundtrip(tmp_path):
    manifest = export_snapshot(tmp_path)
    assert manifest["counts"]["events"] >= 10
    res = import_snapshot(tmp_path)  # повторный импорт идемпотентен (INSERT OR IGNORE)
    assert res and res["imported"]["events"] == manifest["counts"]["events"]
    (tmp_path / "manifest.json").write_text(json.dumps({**manifest, "data_fingerprint": "other"}), encoding="utf-8")
    assert import_snapshot(tmp_path) is None  # снапшот с других данных не применяется
