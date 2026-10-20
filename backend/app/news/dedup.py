"""Дедупликация и склейка новостей в «события».

Три уровня:
1. точный дубль — по хэшу нормализованного текста;
2. перепечатка/пересказ — MinHash по шинглам из «стеммов» лида (оценка Жаккара, устойчива к перестановкам)
   и перекрытие заголовков (устойчиво к перефразированию); берём максимум сигналов;
3. пограничные случаи (похожесть 0,2–0,3) — короткий вопрос дешёвой модели «это одно событие?»
   (только при доступной LLM и с фоновым приоритетом бюджета).
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime, timedelta
from itertools import pairwise

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


# ---------------------------------------------------------------- MinHash
NUM_PERM = 64
_P = (1 << 61) - 1  # простое Мерсенна
_rng = random.Random(20261026)
_PERM = [(_rng.randrange(1, _P), _rng.randrange(0, _P)) for _ in range(NUM_PERM)]
MERGE_AT = 0.3
BORDERLINE_AT = 0.2


def _stem_seq(text: str, prefix: int = 5) -> list[str]:
    out = []
    for w in _word_re.findall(text.lower()):
        w = w.strip(".,")
        if w and w not in STOP and (len(w) >= 3 or any(ch.isdigit() for ch in w)):
            out.append(w[:prefix])
    return out


def shingles(text: str) -> set[str]:
    seq = _stem_seq(text)
    return set(seq) | {f"{a} {b}" for a, b in pairwise(seq)}


def minhash(text: str) -> list[int]:
    sh = shingles(text)
    if not sh:
        return []
    hs = [int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big") for s in sh]
    return [min((a * h + b) % _P for h in hs) for a, b in _PERM]


def minhash_sim(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


def lead(title: str, text: str, words: int = 60) -> str:
    return title + " " + " ".join(text.split()[:words])


def signature(title: str, text: str) -> str:
    return json.dumps(minhash(lead(title, text)))


def match_score(a_title: str, a_text: str, a_time: str | None, a_sig: list[int] | None,
                b_title: str, b_text: str, b_time: str | None, b_sig: list[int] | None, window_days: int = 2) -> float:
    """Итоговая похожесть двух новостей 0..1 (0 — вне окна по времени)."""
    ta, tb = _dt(a_time), _dt(b_time)
    if ta and tb and abs(ta - tb) > timedelta(days=window_days):
        return 0.0
    base = similarity(a_title, lead(a_title, a_text, 40), b_title, lead(b_title, b_text, 40))
    mh = minhash_sim(a_sig or minhash(lead(a_title, a_text)), b_sig or minhash(lead(b_title, b_text)))
    return max(base, mh)
