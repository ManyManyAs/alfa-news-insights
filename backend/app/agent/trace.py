"""Трассировка шагов агента (критерий 12): каждый шаг, инструмент, модель, вход/выход, токены, стоимость."""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.storage import db

MAX_FIELD = 8000


def _short(obj: Any) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_FIELD else s[:MAX_FIELD] + "…[обрезано]"


class Tracer:
    def __init__(self, trace_id: str | None = None, listener=None):
        self.trace_id = trace_id or f"tr-{uuid.uuid4().hex[:12]}"
        self.step_no = 0
        self.steps: list[dict] = []
        self.listener = listener  # callback(event: dict) — для стриминга прогресса агента в UI

    def _emit(self, event: dict) -> None:
        if self.listener:
            try:
                self.listener(event)
            except Exception:  # слушатель не должен ломать агента
                pass

    @contextmanager
    def step(self, name: str, kind: str, input: Any = None, model: str | None = None) -> Iterator[dict]:
        rec: dict = {"name": name, "kind": kind, "input": input, "model": model, "output": None,
                     "tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0}
        t0 = time.perf_counter()
        self._emit({"type": "step_start", "name": name, "kind": kind})
        try:
            yield rec
        except Exception as e:
            rec["output"] = {"error": str(e)}
            raise
        finally:
            rec["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            self.step_no += 1
            rec["step_no"] = self.step_no
            self.steps.append(rec)
            self._emit({"type": "step_end", "name": name, "kind": rec.get("kind", kind), "model": rec.get("model"),
                        "duration_ms": rec["duration_ms"], "fallback": rec.get("fallback"),
                        "cost_rub": round(rec.get("cost_rub") or 0, 4)})
            with db.tx() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO trace_steps(trace_id, step_no, name, kind, model, input, output, tokens_in,"
                    " tokens_out, cost_rub, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (self.trace_id, self.step_no, name, kind, rec.get("model"), _short(rec["input"]), _short(rec["output"]),
                     rec["tokens_in"], rec["tokens_out"], rec["cost_rub"], rec["duration_ms"]),
                )


def load_trace(trace_id: str) -> list[dict]:
    rows = db.query("SELECT * FROM trace_steps WHERE trace_id=? ORDER BY step_no", (trace_id,))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("input", "output"):
            try:
                d[k] = json.loads(d[k]) if d[k] else None
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(d)
    return out
