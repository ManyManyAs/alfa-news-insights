"""Клиент к разрешённым LLM (OpenAI-совместимый API организаторов).

* роутинг «задача → модель» из config/models.yaml, id моделей переопределяются env `LLM_MODEL_<KEY>`;
* дневной бюджет 500 ₽ (сброс в 00:00 МСК): после soft limit — запасная модель, фоновые задачи
  (предрасчёт) не могут съесть резерв интерактивных запросов, после лимита — LLMUnavailable;
* кэш ответов в SQLite (повтор не тратит токены) — он же снапшот для режима replay;
* структурированный вывод: валидация pydantic-схемой и один автоматический «ремонт» ответа;
* стриминг (SSE) для чата; повторы при 429/5xx.

Если LLM недоступна (mock, нет ключа и нет ответа в снапшоте, бюджет, сеть) — бросается
LLMUnavailable, и вызывающий код переходит на детерминированный путь (graceful degradation).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings, load_yaml
from app.storage import db

log = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")
RETRY_STATUS = {429, 500, 502, 503, 504}
# Приоритет запросов: interactive (пользователь ждёт) или background (предрасчёт) — для резерва бюджета
_priority: ContextVar[str] = ContextVar("llm_priority", default="interactive")


@contextmanager
def llm_priority(value: str):
    token = _priority.set(value)
    try:
        yield
    finally:
        _priority.reset(token)


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
    repaired: bool = False

    def json(self) -> dict | list:
        return parse_json(self.text)


def parse_json(text: str) -> dict | list:
    """Достаёт JSON из ответа модели (в т.ч. из ```json ...``` блока или текста вокруг)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()  # reasoning-модели
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


def schema_hint(model: type[BaseModel]) -> str:
    """Компактная JSON-схема для промпта (без служебных полей)."""
    schema = model.model_json_schema()
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))[:6000]


def cache_key(payload: dict) -> str:
    """Ключ кэша не зависит от response_format/stream: повтор без них попадает в тот же кэш."""
    p = {k: v for k, v in payload.items() if k not in ("response_format", "stream")}
    return hashlib.sha256(json.dumps(p, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def budget_day() -> str:
    """Лимит организаторов обновляется в 00:00 по Москве."""
    return datetime.now(MSK).date().isoformat()


def spent_today() -> float:
    row = db.query_one("SELECT COALESCE(SUM(cost_rub),0) AS s FROM llm_usage WHERE day=?", (budget_day(),))
    return float(row["s"]) if row else 0.0


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.cfg = load_yaml("models.yaml")
        # id моделей у провайдера можно переопределить без правки конфига: LLM_MODEL_GEMMA=...
        for key, m in self.cfg["models"].items():
            override = os.environ.get(f"LLM_MODEL_{key.upper()}")
            if override:
                m["id"] = override
        self.allowed_ids = {m["id"] for m in self.cfg["models"].values()}

    # --- режим ---------------------------------------------------------
    @property
    def mode(self) -> str:
        """live — реальные вызовы; replay — только ответы из снапшота/кэша (для жюри без ключа);
        mock — без LLM вообще (тесты). auto = live при наличии ключа, иначе replay."""
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

    # --- бюджет и выбор модели ----------------------------------------
    def budget_status(self) -> dict:
        budget = float(self.cfg.get("daily_budget_rub", 500))
        spent = spent_today()
        return {"day": budget_day(), "spent_rub": round(spent, 2), "budget_rub": budget,
                "soft_limit_rub": budget * float(self.cfg.get("soft_limit_share", 0.8)),
                "background_limit_rub": budget * float(self.cfg.get("background_share", 0.6)),
                "left_rub": round(max(0.0, budget - spent), 2)}

    def model_for(self, task: str, priority: str = "interactive") -> tuple[str, dict]:
        route = self.cfg["tasks"].get(task) or {"model": "qwen", "fallback": "gemma"}
        b = self.budget_status()
        if b["spent_rub"] >= b["budget_rub"]:
            raise LLMUnavailable(f"Дневной бюджет исчерпан: {b['spent_rub']:.1f} из {b['budget_rub']} ₽")
        if priority == "background" and b["spent_rub"] >= b["background_limit_rub"]:
            raise LLMUnavailable("Резерв бюджета оставлен для запросов пользователей — фоновая задача отложена")
        key = route["fallback"] if b["spent_rub"] >= b["soft_limit_rub"] else route["model"]
        model = self.cfg["models"][key]
        if model["id"] not in self.allowed_ids:  # защита от подмены конфигурации
            raise LLMUnavailable(f"Модель {model['id']} не входит в список разрешённых")
        return key, model

    def _candidates(self, task: str, priority: str | None, model_key: str | None = None) -> list[dict]:
        priority = priority or _priority.get()
        route = self.cfg["tasks"].get(task) or {"model": "qwen", "fallback": "gemma"}
        if model_key:  # явный выбор модели (бенчмарк) — бюджет проверяется так же
            if self.live:
                self.model_for(task, priority)
            return [self.cfg["models"][model_key]]
        if self.live:
            return [self.model_for(task, priority)[1]]
        # replay: бюджет не тратится — ищем сохранённый ответ основной и запасной модели
        return [self.cfg["models"][route["model"]], self.cfg["models"][route["fallback"]]]

    def _cached(self, candidates: list[dict], messages, json_mode, max_tokens, temperature) -> LLMResult | None:
        for model in candidates:
            payload = self._payload(model["id"], messages, json_mode, max_tokens, temperature)
            hit = db.query_one("SELECT response FROM llm_cache WHERE key=?", (cache_key(payload),))
            if hit:
                return LLMResult(text=json.loads(hit["response"])["text"], model=model["id"], cached=True)
        return None

    # --- вызов ---------------------------------------------------------
    def complete(self, task: str, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 1200,
                 temperature: float = 0.2, priority: str | None = None, model_key: str | None = None) -> LLMResult:
        if not self.enabled:
            raise LLMUnavailable("LLM_MODE=mock: генеративные модели отключены")
        candidates = self._candidates(task, priority, model_key)
        hit = self._cached(candidates, messages, json_mode, max_tokens, temperature)
        if hit:
            return hit
        if not self.live:
            raise LLMUnavailable("replay: ответа нет в снапшоте — используется детерминированный режим")
        model = candidates[0]
        payload = self._payload(model["id"], messages, json_mode, max_tokens, temperature)
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
        text = (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        usage = resp.get("usage") or {}
        res = self._finish(task, model, messages, payload, text, usage)
        res.duration_ms = int((time.perf_counter() - t0) * 1000)
        return res

    def complete_json(self, task: str, messages: list[dict], schema: type[BaseModel] | None = None, *,
                      max_tokens: int = 1500, temperature: float = 0.1, priority: str | None = None,
                      repair: bool = True, model_key: str | None = None) -> tuple[dict, LLMResult]:
        """JSON-ответ с валидацией по pydantic-схеме и одной попыткой «ремонта» (модель видит свою ошибку)."""
        res = self.complete(task, messages, json_mode=True, max_tokens=max_tokens, temperature=temperature,
                            priority=priority, model_key=model_key)
        try:
            data = res.json()
            if schema is not None:
                data = schema.model_validate(data).model_dump()
            return data, res
        except (ValueError, ValidationError) as err:
            if not repair:
                raise ValueError(f"Невалидный JSON: {err}") from err
            fix = [*messages, {"role": "assistant", "content": res.text[:6000]},
                   {"role": "user", "content": f"Ответ не прошёл проверку: {str(err)[:1500]}\n"
                                               "Верни ТОЛЬКО исправленный JSON строго по схеме, без пояснений."}]
            res2 = self.complete(task, fix, json_mode=True, max_tokens=max_tokens, temperature=0.0, priority=priority,
                                 model_key=model_key or next(k for k, m in self.cfg["models"].items() if m["id"] == res.model))
            data = res2.json()
            if schema is not None:
                data = schema.model_validate(data).model_dump()
            res2.repaired = True
            res2.tokens_in += res.tokens_in
            res2.tokens_out += res.tokens_out
            res2.cost_rub += res.cost_rub
            return data, res2

    def stream(self, task: str, messages: list[dict], *, max_tokens: int = 800, temperature: float = 0.3,
               priority: str | None = None) -> Iterator[str]:
        """Потоковый ответ (чат). Из кэша/снапшота отдаётся кусками, вживую — SSE от провайдера."""
        if not self.enabled:
            raise LLMUnavailable("LLM_MODE=mock")
        candidates = self._candidates(task, priority)
        hit = self._cached(candidates, messages, False, max_tokens, temperature)
        if hit:
            for i in range(0, len(hit.text), 40):
                yield hit.text[i : i + 40]
            return
        if not self.live:
            raise LLMUnavailable("replay: ответа нет в снапшоте")
        model = candidates[0]
        payload = {**self._payload(model["id"], messages, False, max_tokens, temperature), "stream": True}
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        parts: list[str] = []
        usage: dict = {}
        try:
            with httpx.Client(timeout=self.settings.llm_timeout_s) as c, \
                    c.stream("POST", url, json=payload, headers=self._headers()) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    usage = obj.get("usage") or usage
                    delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if delta:
                        parts.append(delta)
                        yield delta
        except httpx.HTTPError as e:
            if not parts:  # стриминг не поддержан — обычный запрос
                res = self.complete(task, messages, max_tokens=max_tokens, temperature=temperature, priority=priority)
                yield res.text
                return
            raise LLMUnavailable(f"Обрыв стрима: {e}") from e
        payload.pop("stream", None)
        self._finish(task, model, messages, payload, "".join(parts), usage)

    # --- служебное ------------------------------------------------------
    def _finish(self, task: str, model: dict, messages, payload: dict, text: str, usage: dict) -> LLMResult:
        tin = int(usage.get("prompt_tokens") or _approx_tokens(messages))
        tout = int(usage.get("completion_tokens") or _approx_tokens(text))
        cost = tin / 1000 * model["price_rub_per_1k_input"] + tout / 1000 * model["price_rub_per_1k_output"]
        self._account(model["id"], tin, tout, cost)
        with db.tx() as conn:
            conn.execute("INSERT OR REPLACE INTO llm_cache(key, task, model, response) VALUES (?,?,?,?)",
                         (cache_key(payload), task, model["id"], db.dumps({"text": text})))
        return LLMResult(text=text, model=model["id"], tokens_in=tin, tokens_out=tout, cost_rub=cost)

    @staticmethod
    def _payload(model_id: str, messages: list[dict], json_mode: bool, max_tokens: int, temperature: float) -> dict:
        payload: dict[str, Any] = {"model": model_id, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.llm_api_key}"}

    def _post(self, payload: dict, attempts: int = 3) -> dict:
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        delay = 1.5
        for i in range(attempts):
            with httpx.Client(timeout=self.settings.llm_timeout_s) as client:
                r = client.post(url, json=payload, headers=self._headers())
            if r.status_code in RETRY_STATUS and i < attempts - 1:
                wait = float(r.headers.get("retry-after") or delay)
                log.warning("LLM API %s, повтор через %.1f с", r.status_code, wait)
                time.sleep(min(wait, 20))
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        raise httpx.HTTPError("LLM API: исчерпаны повторы")

    def list_models(self) -> list[str]:
        """Какие модели отдаёт эндпоинт организаторов (для сверки id)."""
        url = self.settings.llm_base_url.rstrip("/") + "/models"
        with httpx.Client(timeout=30) as c:
            r = c.get(url, headers=self._headers())
            r.raise_for_status()
            return [m.get("id") for m in r.json().get("data", [])]

    @staticmethod
    def _account(model_id: str, tin: int, tout: int, cost: float) -> None:
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO llm_usage(day, model, calls, tokens_in, tokens_out, cost_rub) VALUES (?,?,1,?,?,?)
                   ON CONFLICT(day, model) DO UPDATE SET calls=calls+1, tokens_in=tokens_in+excluded.tokens_in,
                   tokens_out=tokens_out+excluded.tokens_out, cost_rub=cost_rub+excluded.cost_rub""",
                (budget_day(), model_id, tin, tout, cost),
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
