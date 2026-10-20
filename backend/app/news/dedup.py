"""Дедупликация и склейка новостей в «события».

Две новости — одно событие, если близки по лексике (Жаккар по «стеммам» заголовка+лида)
и опубликованы в пределах окна по времени. Точные дубли отсекаются по хэшу текста.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

_word_re = re.compile(r"[a-zа-яё0-9%,.]+", re.I)
STOP = set("и в во на по с со за из к от до для о об что как это его ее её их не но а же ли то бы был была были будет "
           "после также при уже года году год г млрд млн руб рублей".split())


def content_hash(title: str, text: str) -> str:
    norm = re.sub(r"\s+", " ", f"{title} {text}".lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()


def stems(text: str, prefix: int = 5) -> set[str]:
    out = set()
    for w in _word_re.findall(text.lower()):
        w = w.strip(".,")
        if not w or w in STOP or (len(w) < 3 and not any(ch.isdigit() for ch in w)):
            continue
        out.add(w[:prefix])
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def same_event(a_title: str, a_text: str, a_time: str | None, b_title: str, b_text: str, b_time: str | None,
               threshold: float = 0.3, window_days: int = 2) -> bool:
    ta, tb = _dt(a_time), _dt(b_time)
    if ta and tb and abs(ta - tb) > timedelta(days=window_days):
        return False
    lead_a = a_title + " " + " ".join(a_text.split()[:40])
    lead_b = b_title + " " + " ".join(b_text.split()[:40])
    return similarity(a_title, lead_a, b_title, lead_b) >= threshold


def overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def similarity(a_title: str, a_lead: str, b_title: str, b_lead: str) -> float:
    """Смесь перекрытия заголовков (устойчиво к перефразированию) и Жаккара по лиду."""
    return 0.6 * overlap(stems(a_title), stems(b_title)) + 0.4 * jaccard(stems(a_lead), stems(b_lead))
