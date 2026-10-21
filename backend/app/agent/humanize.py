"""Человеческий текст для клиента: без внутренних идентификаторов источников, кодов продуктов и факторов.

Модель и правила ссылаются на источники id вида news:…, bank:…, graph:in:71, profile:CL_PRODUCT_LOA_MARK —
это нужно для проверки обоснованности и остаётся в поле sources каждого утверждения (в интерфейсе — метки
«новость», «банк», «контрагенты»). В самом тексте такие id клиенту не нужны: убираем их или заменяем словами.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.config import load_yaml

REF = r"(?:news|bank|graph|profile|calc|peers|forecast|doc|insight|user):[^\s,;()\[\]«»]+"
_GROUP = re.compile(r"\s*[(\[]\s*(?:см\.\s*)?" + REF + r"(?:\s*(?:[,;]|и)\s*" + REF + r")*\s*[)\]]")
_REF = re.compile(REF)
_THESIS = re.compile(r"тезис(?:ом|у|а|ы|ов)?\s+(?:Альфа-)?банка\s+(?:bank:)?t\d+\b", re.I)
_OLD_DIV = re.compile(r"Событие \((\w+): (up|down)\) расходится с тезисом банка \S+ \((up|down)\)[^.]*")
_NEW_DIV = re.compile(r"Новость говорит о «(\w+)» по фактору (\w+), а тезис банка \S+ \(([^)]*)\) — о «(\w+)»\.?[^.]*\.?")
_PRODUCTS = {"CL_PRODUCT_LOA_MARK": "кредит", "CL_PRODUCT_DEP_MARK": "депозит", "CL_PRODUCT_LEA_MARK": "лизинг",
             "CL_PRODUCT_ZPP_MARK": "зарплатный проект", "CL_PRODUCT_GAR_MARK": "банковские гарантии",
             "CL_PRODUCT_RKO_MARK": "расчётный счёт", "FACTORING_MARK": "факторинг", "FTS_FLAG": "ВЭД"}
# названия полей контекста, которые модель иногда цитирует
_FIELDS = {"top_exposures": "ключевые факторы компании", "top_buyer_share": "доля крупнейшего покупателя",
           "top_supplier_share": "доля крупнейшего поставщика", "industry_mix": "отрасли контрагентов",
           "unknown_share_in": "доля покупателей с неизвестной отраслью",
           "unknown_share_out": "доля поставщиков с неизвестной отраслью", "hhi_buyers": "концентрация покупателей",
           "hhi_suppliers": "концентрация поставщиков", "n_buyers": "число покупателей", "n_suppliers": "число поставщиков",
           "network_percentile": "место в сети платежей", "affiliated_companies": "аффилированные компании",
           "top_counterparties": "крупнейшие контрагенты", "allowed_sources": "источники",
           "check_consistency": "сверка с позицией банка",
           "buyer_concentration_vs_peers": "концентрация покупателей в сравнении", "median_revenue": "медианная выручка",
           "event_brief": "разбор события"}
_DIR = {"up": "рост", "down": "снижение", "рост": "рост", "снижение": "снижение"}
# поля, где лежат id, коды и служебные значения — их не трогаем
SKIP = {"sources", "id", "basis", "factor", "trace_id", "event_id", "company_uid", "generated_by", "kind", "confidence",
        "effect", "lever", "name", "product", "action_type", "type", "url", "params", "scores", "direction", "role",
        "indirect", "thesis_ref", "monthly", "months"}


@lru_cache(maxsize=1)
def _factor_titles() -> dict[str, str]:
    return {k: v["title"] for k, v in load_yaml("factors.yaml")["factors"].items()}


def _inline(ref: str) -> str:
    kind, _, rest = ref.partition(":")
    if kind == "profile":
        if rest.startswith("OKVED"):
            return "ОКВЭД " + rest.split("=", 1)[-1] if "=" in rest else "отрасль компании"
        return _PRODUCTS.get(rest, "")
    return ""


def humanize_text(s: str) -> str:
    if not s or not isinstance(s, str) or (":" not in s and "_" not in s and "OKVED" not in s and "банка" not in s):
        return s
    titles = _factor_titles()

    def old_div(m: re.Match) -> str:
        f = titles.get(m.group(1), m.group(1))
        return (f"По теме «{f}» новость говорит о {'росте' if m.group(2) == 'up' else 'снижении'}, а прогноз "
                f"Альфа-Банка — о {'росте' if m.group(3) == 'up' else 'снижении'}. Возможно, речь о разных горизонтах")

    def new_div(m: re.Match) -> str:
        f = titles.get(m.group(2), m.group(2))
        horizon = m.group(3) if m.group(3) and "не указан" not in m.group(3) else ""
        a, b = _DIR.get(m.group(1), m.group(1)), _DIR.get(m.group(4), m.group(4))
        return (f"По теме «{f}» новость говорит о {'росте' if a == 'рост' else 'снижении'}, а Альфа-Банк"
                f"{f' (прогноз на {horizon})' if horizon else ''} — о {'росте' if b == 'рост' else 'снижении'}. "
                f"Возможно, речь о разных горизонтах.")

    s = _OLD_DIV.sub(old_div, s)
    s = _NEW_DIV.sub(new_div, s)
    s = _THESIS.sub("позиция Альфа-Банка", s)
    s = _GROUP.sub("", s)
    s = re.sub(r"\s*\((\w+_\w+|\w+):\s*(?:up|down|stable|unknown)\)", "", s)
    s = re.sub(r"(?:,?\s*см\.\s*)?" + REF, lambda m: _inline(m.group(0).split()[-1]), s)
    s = re.sub(r"\b(" + "|".join(_FIELDS) + r")\b", lambda m: _FIELDS[m.group(1)], s)
    s = re.sub(r"\bOKVED\s*=?\s*", "ОКВЭД ", s)
    s = re.sub(r"\b(?:CL_PRODUCT_[A-Z]+_MARK|FACTORING_MARK|FTS_FLAG)\b", lambda m: _PRODUCTS.get(m.group(0), ""), s)
    s = re.sub(r"\b(" + "|".join(map(re.escape, titles)) + r")\b",
               lambda m: f"«{titles[m.group(1)]}»" if "_" in m.group(1) else m.group(1), s)
    s = re.sub(r"(концентрация покупателей в сравнении|медианная выручка) есть у \d+%", r"\1", s)
    # уборка после вырезания: пустые скобки, двойные пробелы, пробел перед знаками
    s = re.sub(r"\(\s*[,;]?\s*\)|\[\s*\]", "", s)
    s = re.sub(r"\(\s*[,;]\s*", "(", s)
    s = re.sub(r"\s*[,;]\s*\)", ")", s)
    # банк не считает эффект для конкретной компании — это оценка калькулятора сервиса
    s = re.sub(r"[Пп]о (расчёту|оценке) банка(?=(?:[^.]|\.(?!\s[А-ЯЁ])){0,100}?(?:₽|долг\s*≈|выручки))",
               lambda m: ("П" if m.group(0)[0] == "П" else "п") + "о оценке сервиса", s)
    s = re.sub(r"оценка банка: долг", "оценка сервиса: долг", s)
    s = re.sub(r"\s+([,.;:!?)])", r"\1", s)
    s = re.sub(r"([(«])\s+", r"\1", s)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def humanize(obj, key: str | None = None):
    """Рекурсивно чистит строки во всех текстовых полях (dict/list), кроме служебных."""
    if key == "sources" and isinstance(obj, list) and obj and isinstance(obj[0], dict):  # SourceRef: чистим подписи
        return [{**x, "title": humanize_text(x.get("title", ""))} for x in obj]
    if key in SKIP:
        return obj
    if isinstance(obj, str):
        return humanize_text(obj)
    if isinstance(obj, list):
        return [humanize(x, key) for x in obj]
    if isinstance(obj, dict):
        return {k: humanize(v, k) for k, v in obj.items()}
    return obj


def humanize_insight(ins):
    """Insight → тот же Insight с человеческими текстами (sources и id не меняются)."""
    return type(ins).model_validate(humanize(ins.model_dump()))
