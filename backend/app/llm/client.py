"""Клиент к разрешённым LLM (OpenAI-совместимый API организаторов).

* роутинг задача -> модель из config/models.yaml;
* контроль дневного бюджета (500 ₽): после soft limit — запасная модель, после лимита — LLMUnavailable;
* кэш ответов в SQLite (повторный запрос не тратит токены);
* учёт токенов/стоимости для трассировки и дашборда.

Если LLM недоступна (LLM_MODE=mock, нет ключа, бюджет исчерпан, ошибка сети) — бросается
LLMUnavailable, и вызывающий код переходит на детерминированный путь (graceful degradation).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date

import httpx

from app.config import get_settings, load_yaml
from app.storage import db

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_rub: float = 0.0
    cached: bool = False
    duration_ms: int = 0

    def json(self) -> dict | list:
        return parse_json(self.text)


def parse_json(text: str) -> dict | list:
    """Достаёт JSON из ответа модели (в т.ч. из ```json ...``` блока)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start, end = text.find(open_ch), text.rfind(close_ch)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
    raise ValueError("Ответ модели не является JSON")


def cache_key(payload: dict) -> str:
    """Ключ кэша не зависит от response_format: повтор без него (fallback) попадает в тот же кэш."""
    p = {k: v for k, v in payload.items() if k != "response_format"}
    return hashlib.sha256(json.dumps(p, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def spent_today() -> float:
    row = db.query_one("SELECT COALESCE(SUM(cost_rub),0) AS s FROM llm_usage WHERE day=?", (date.today().isoformat(),))
    return float(row["s"]) if row else 0.0


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.cfg = load_yaml("models.yaml")
        self.allowed_ids = {m["id"] for m in self.cfg["models"].values()}

    # --- выбор модели -------------------------------------------------
    @property
    def mode(self) -> str:
        """Итоговый режим: live — реальные вызовы; replay — только ответы из снапшота/кэша
        (для жюри без ключа); mock — без LLM вообще (тесты). auto = live при наличии ключа, иначе replay."""
        m = (self.settings.llm_mode or "auto").lower()
        if m == "auto":
            return "live" if self.settings.llm_api_key else "replay"
        if m == "live" and not self.settings.llm_api_key:
            return "replay"
        return m if m in ("live", "replay", "mock") else "mock"

    @property
    def live(self) -> bool:
        return self.mode == "live"

    @property
    def enabled(self) -> bool:
        """Есть смысл пытаться звать LLM (вживую или из снапшота)."""
        return self.mode in ("live", "replay")

    def model_for(self, task: str) -> tuple[str, dict]:
        route = self.cfg["tasks"].get(task) or {"model": "qwen", "fallback": "gemma"}
        budget = float(self.cfg.get("daily_budget_rub", 500))
        spent = spent_today()
        if spent >= budget:
            raise LLMUnavailable(f"Дневной бюджет исчерпан: {spent:.1f} из {budget} ₽")
        key = route["fallback"] if spent >= budget * float(self.cfg.get("soft_limit_share", 0.8)) else route["model"]
        model = self.cfg["models"][key]
        if model["id"] not in self.allowed_ids:  # защита от подмены конфигурации
            raise LLMUnavailable(f"Модель {model['id']} не входит в список разрешённых")
        return key, model

    # --- вызов --------------------------------------------------------
    def complete(self, task: str, messages: list[dict], *, json_mode: bool = False,
                 max_tokens: int = 1200, temperature: float = 0.2) -> LLMResult:
        if not self.enabled:
            raise LLMUnavailable("LLM_MODE=mock: генеративные модели отключены")
        route = self.cfg["tasks"].get(task) or {"model": "qwen", "fallback": "gemma"}
        # В replay бюджет не тратится — ищем ответ основной и запасной модели
        candidates = [self.cfg["models"][route["model"]], self.cfg["models"][route["fallback"]]] if not self.live \
            else [self.model_for(task)[1]]
        for model in candidates:
            payload = self._payload(model["id"], messages, json_mode, max_tokens, temperature)
            hit = db.query_one("SELECT response FROM llm_cache WHERE key=?", (cache_key(payload),))
            if hit:
                data = json.loads(hit["response"])
                return LLMResult(text=data["text"], model=model["id"], cached=True)
        if not self.live:
            raise LLMUnavailable("replay: ответа нет в снапшоте — используется детерминированный режим")
        model = candidates[0]
        payload = self._payload(model["id"], messages, json_mode, max_tokens, temperature)
        ck = cache_key(payload)

        t0 = time.perf_counter()
        try:
            resp = self._post(payload)
        except httpx.HTTPError as e:
            if json_mode:  # не все эндпоинты поддерживают response_format — повторяем без него
                payload.pop("response_format", None)
                try:
                    resp = self._post(payload)
                except httpx.HTTPError as e2:
                    raise LLMUnavailable(f"Ошибка LLM API: {e2}") from e2
            else:
                raise LLMUnavailable(f"Ошибка LLM API: {e}") from e
        text = resp["choices"][0]["message"]["content"] or ""
        usage = resp.get("usage") or {}
        tin = int(usage.get("prompt_tokens") or _approx_tokens(messages))
        tout = int(usage.get("completion_tokens") or _approx_tokens(text))
        cost = tin / 1000 * model["price_rub_per_1k_input"] + tout / 1000 * model["price_rub_per_1k_output"]
        self._account(model["id"], tin, tout, cost)
        with db.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO llm_cache(key, task, model, response) VALUES (?,?,?,?)",
                         (ck, task, model["id"], db.dumps({"text": text})))
        return LLMResult(text=text, model=model["id"], tokens_in=tin, tokens_out=tout, cost_rub=cost,
                         duration_ms=int((time.perf_counter() - t0) * 1000))

    @staticmethod
    def _payload(model_id: str, messages: list[dict], json_mode: bool, max_tokens: int, temperature: float) -> dict:
        payload = {"model": model_id, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post(self, payload: dict) -> dict:
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        with httpx.Client(timeout=self.settings.llm_timeout_s) as client:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _account(model_id: str, tin: int, tout: int, cost: float) -> None:
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO llm_usage(day, model, calls, tokens_in, tokens_out, cost_rub) VALUES (?,?,1,?,?,?)
                   ON CONFLICT(day, model) DO UPDATE SET calls=calls+1, tokens_in=tokens_in+excluded.tokens_in,
                   tokens_out=tokens_out+excluded.tokens_out, cost_rub=cost_rub+excluded.cost_rub""",
                (date.today().isoformat(), model_id, tin, tout, cost),
            )


def _approx_tokens(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return max(1, len(s) // 3)


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm() -> None:
    global _client
    _client = None
