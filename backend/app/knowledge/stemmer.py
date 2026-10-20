"""Русский стеммер Snowball (алгоритм Портера для русского) без внешних зависимостей.

Нужен для BM25 по аналитике банка: «Китаем» и «Китай», «юанях» и «юани», «снижать» и «снижение»
должны совпадать. https://snowballstem.org/algorithms/russian/stemmer.html
"""
from __future__ import annotations

import re
from functools import lru_cache

VOWELS = set("аеиоуыэюя")

PERFECTIVE_1 = ("вшись", "вши", "в")  # после а/я
PERFECTIVE_2 = ("ившись", "ывшись", "ивши", "ывши", "ив", "ыв")
REFLEXIVE = ("ся", "сь")
ADJECTIVE = ("ими", "ыми", "его", "ого", "ему", "ому", "ее", "ие", "ые", "ое", "ей", "ий", "ый", "ой", "ем", "им",
             "ым", "ом", "их", "ых", "ую", "юю", "ая", "яя", "ою", "ею")
PARTICIPLE_1 = ("ем", "нн", "вш", "ющ", "щ")  # после а/я
PARTICIPLE_2 = ("ивш", "ывш", "ующ")
VERB_1 = ("ете", "йте", "ешь", "нно", "ла", "на", "ли", "ем", "ло", "но", "ет", "ют", "ны", "ть", "й", "л", "н")  # после а/я
VERB_2 = ("ейте", "уйте", "ила", "ыла", "ена", "ите", "или", "ыли", "ило", "ыло", "ено", "ует", "уют", "ены", "ить",
          "ыть", "ишь", "ей", "уй", "ил", "ыл", "им", "ым", "ен", "ят", "ит", "ыт", "ую", "ю")
NOUN = ("иями", "ями", "ами", "ией", "иям", "ием", "иях", "ев", "ов", "ие", "ье", "еи", "ии", "ей", "ой", "ий", "ям",
        "ем", "ам", "ом", "ах", "ях", "ию", "ью", "ия", "ья", "а", "е", "и", "й", "о", "у", "ы", "ь", "ю", "я")
SUPERLATIVE = ("ейше", "ейш")
DERIVATIONAL = ("ость", "ост")


def _regions(w: str) -> tuple[int, int]:
    """Индексы начала RV и R2."""
    rv = next((i + 1 for i, c in enumerate(w) if c in VOWELS), len(w))

    def after_vc(start: int) -> int:
        for i in range(start + 1, len(w)):
            if w[i] not in VOWELS and w[i - 1] in VOWELS:
                return i + 1
        return len(w)

    r1 = after_vc(0)
    r2 = after_vc(r1) if r1 < len(w) else len(w)
    return rv, r2


def _strip(w: str, rv: int, endings: tuple[str, ...], after_a: bool = False) -> str | None:
    for e in sorted(endings, key=len, reverse=True):
        if w.endswith(e) and len(w) - len(e) >= rv:
            if after_a:
                pos = len(w) - len(e) - 1
                if pos < rv or w[pos] not in "ая":
                    continue
            return w[: len(w) - len(e)]
    return None


@lru_cache(maxsize=50000)
def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if len(w) < 3 or not re.fullmatch(r"[а-я]+", w):
        return w
    rv, r2 = _regions(w)
    # Шаг 1
    res = _strip(w, rv, PERFECTIVE_1, True) or _strip(w, rv, PERFECTIVE_2)
    if res is not None:
        w = res
    else:
        w = _strip(w, rv, REFLEXIVE) or w
        adj = _strip(w, rv, ADJECTIVE)
        if adj is not None:
            w = _strip(adj, rv, PARTICIPLE_1, True) or _strip(adj, rv, PARTICIPLE_2) or adj
        else:
            verb = _strip(w, rv, VERB_1, True) or _strip(w, rv, VERB_2)
            w = verb if verb is not None else (_strip(w, rv, NOUN) or w)
    # Шаг 2
    if w.endswith("и") and len(w) - 1 >= rv:
        w = w[:-1]
    # Шаг 3
    der = next((e for e in DERIVATIONAL if w.endswith(e) and len(w) - len(e) >= r2), None)
    if der:
        w = w[: -len(der)]
    # Шаг 4
    sup = _strip(w, rv, SUPERLATIVE)
    if sup is not None:
        w = sup
    if (w.endswith("нн") and len(w) - 1 >= rv) or (w.endswith("ь") and len(w) - 1 >= rv):
        w = w[:-1]
    return w


_TOKEN = re.compile(r"[a-zа-яё0-9]+(?:[.,][0-9]+)?%?", re.I)
STOP = set("и в во на по с со за из к от до для о об что как это его ее её их не но а же ли то бы был была были будет "
           "после также при уже года году год г млрд млн руб рублей мы наш нашем который которые этом этот этого".split())


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        if t in STOP or (len(t) < 2 and not t[0].isdigit()):
            continue
        out.append(stem(t))
    return out
