"""Проверка доступа к моделям организаторов: id, базовый ответ, JSON-режим, vision, латентность.

  python scripts/llm_check.py            # нужен LLM_API_KEY и LLM_BASE_URL
Результат: docs/llm/llm_check.md и docs/llm/llm_check.json (без ключей). Стоимость — копейки.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import REPO_ROOT
from app.llm.client import LLMUnavailable, get_llm, parse_json, redact

OUT = REPO_ROOT / "docs" / "llm"


def red_square_png(size: int = 32) -> str:
    """PNG без внешних библиотек: красный квадрат — проверка vision."""
    raw = b"".join(b"\x00" + b"\xdd\x22\x22" * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def match_ids(configured: dict[str, str], available: list[str]) -> dict[str, str | None]:
    """Сопоставляем id из конфига со списком эндпоинта (точно, без регистра, по ключевым словам)."""
    norm = {a.lower(): a for a in available}
    out = {}
    for key, cid in configured.items():
        if cid in available:
            out[key] = cid
        elif cid.lower() in norm:
            out[key] = norm[cid.lower()]
        else:
            words = [w for w in cid.lower().replace("_", "-").split("-") if len(w) > 1]
            best = max(available, key=lambda a: sum(w in a.lower() for w in words), default=None)
            out[key] = best if best and sum(w in best.lower() for w in words) >= 2 else None
    return out


def timed(fn):
    t0 = time.perf_counter()
    try:
        return fn(), round(time.perf_counter() - t0, 2), None
    except Exception as e:
        return None, round(time.perf_counter() - t0, 2), redact(str(e))[:300]


def main() -> None:
    llm = get_llm()
    if not llm.live:
        sys.exit("Нужен LLM_API_KEY (режим live)")
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"checked_at": datetime.now(UTC).isoformat(), "models": {}}
    available, _, err = timed(llm.list_models)
    report["endpoint_models"] = available or []
    report["endpoint_models_error"] = err
    configured = {k: m["id"] for k, m in llm.cfg["models"].items()}
    report["id_match"] = match_ids(configured, available or []) if available else {}
    for key, found in report["id_match"].items():  # проверяем найденные id, даже если в конфиге другие
        if found and found != configured[key]:
            llm.cfg["models"][key]["id"] = found
            llm.allowed_ids.add(found)

    for key, m in llm.cfg["models"].items():
        r: dict = {"configured_id": m["id"]}
        ping, r["ping_s"], r["ping_error"] = timed(lambda key=key: llm.complete(
            "chat", [{"role": "user", "content": "Ответь одним словом: ОК"}], max_tokens=300, model_key=key))
        r["ping"] = ping.text.strip()[:40] if ping else None
        res, r["json_s"], r["json_error"] = timed(lambda key=key: llm.complete(
            "classify_news", [{"role": "system", "content": "Верни только JSON."},
                              {"role": "user", "content": 'Верни JSON {"factor": "key_rate", "direction": "up"}'}],
            json_mode=True, max_tokens=400, model_key=key))
        try:
            r["json_ok"] = bool(res) and isinstance(parse_json(res.text), dict)
        except ValueError:
            r["json_ok"] = False
        if key == "qwen_vl":
            msg = [{"role": "user", "content": [
                {"type": "text", "text": "Какого цвета квадрат на картинке? Ответь одним словом."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{red_square_png()}"}}]}]
            vis, r["vision_s"], r["vision_error"] = timed(lambda msg=msg: llm.complete("ocr_page", msg, max_tokens=300, model_key="qwen_vl"))
            r["vision"] = vis.text.strip()[:40] if vis else None
            r["vision_ok"] = bool(vis) and "крас" in vis.text.lower()
        report["models"][key] = r
        print(key, json.dumps(r, ensure_ascii=False))

    report["budget"] = llm.budget_status()
    (OUT / "llm_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Проверка моделей организаторов", "", f"Дата: {report['checked_at']}", "",
             f"Модели эндпоинта: {', '.join(report['endpoint_models']) or report['endpoint_models_error']}", "",
             "| Ключ | id в конфиге | Найден на эндпоинте | Ответ | Пинг, с | JSON | Vision |", "|---|---|---|---|---|---|---|"]
    for key, r in report["models"].items():
        found = report["id_match"].get(key) if report["id_match"] else "—"
        vision = ("✓" if r.get("vision_ok") else "✗") if key == "qwen_vl" else ""
        lines.append(f"| {key} | `{r['configured_id']}` | {found or '❌ не найден'} | {r['ping'] or r['ping_error']} | "
                     f"{r['ping_s']} | {'✓' if r['json_ok'] else '✗'} | {vision} |")
    mism = {k: v for k, v in report.get("id_match", {}).items() if v and v != configured[k]}
    if mism:
        lines += ["", "Рекомендуемые переопределения (repo variables / .env):", ""]
        lines += [f"- `LLM_MODEL_{k.upper()}={v}`" for k, v in mism.items()]
    lines += ["", f"Расход на проверку: {report['budget']['spent_rub']} ₽"]
    (OUT / "llm_check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except LLMUnavailable as e:
        sys.exit(f"LLM недоступна: {e}")
