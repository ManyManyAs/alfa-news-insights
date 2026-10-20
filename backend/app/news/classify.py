"""Классификация события: факторы (из config/factors.yaml), направление, факты vs прогнозы, важность.

Основной путь — Gemma 4 (дёшево, JSON). Запасной — правила по ключевым словам (без LLM).
"""
from __future__ import annotations

import logging
import re

from app.agent.llm_schemas import ClassificationOut
from app.config import load_yaml
from app.llm.client import LLMUnavailable, get_llm
from app.news.entities import REGION_PATTERNS, extract_effective_dates, extract_industries, extract_regions
from app.news.models import EventClassification, FactorHit, NewsItem
from app.security.guard import UNTRUSTED_NOTICE, sanitize_untrusted

log = logging.getLogger(__name__)

UP = re.compile(r"вырос|выросл|повыс|повыш|увелич|ускор|подорож|рост[а-я]*|раст[уеё]|расти|подня|укрепл|расширя|ужесточ|"
                r"превыс|обновил[аио]? (?:исторический |новый )?(?:максимум|рекорд)", re.I)
DOWN = re.compile(r"сниз|упал|упад|замедл|подешев|дефляц|сократ|ослаб|охлажд|падени|опусти|обвал", re.I)
STABLE = re.compile(r"сохранил|сохранен|сохранив|без изменени|оставил|осталс|осталась|неизменн|пауз", re.I)
FORECAST = re.compile(r"ожида|прогноз|может|могут|вероятно|полага|считают|по мнению|рассматрива|обсужда|планиру", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|п\.п\.|б\.п\.|млрд|млн|трлн|руб|\$)", re.I)
COUNTRIES = ["Китай", "Турция", "ОАЭ", "Индия", "США", "Казахстан", "Узбекистан", "Беларусь", "Гонконг", "Иран"]
OFFICIAL_HINTS = re.compile(r"банк росси|цб|минфин|росстат|правительств|минэконом|фнс", re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _dir_of(text: str) -> str:
    if STABLE.search(text):
        return "stable"
    up, down = bool(UP.search(text)), bool(DOWN.search(text))
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    if re.search(r"(?<![а-яё])(дефляц|инфляц)", text, re.I):  # «дефляция» сама задаёт направление цен
        return "down" if "дефляц" in text.lower() else "unknown"
    return "unknown"


def _direction(sentence: str, rx: re.Pattern) -> str:
    """Направление определяем сначала по фрагменту с ключевым словом, затем по всему предложению."""
    for clause in re.split(r"[,;:—–]| и ", sentence):
        if rx.search(clause):
            d = _dir_of(clause)
            if d != "unknown":
                return d
    return _dir_of(sentence)


def _kw_re(kws: list[str]) -> re.Pattern:
    """Ключевое слово — начало слова: «ставк» найдёт «ставки», но «стал» не найдёт «установлен»."""
    return re.compile(r"(?<![а-яёa-z0-9])(?:" + "|".join(re.escape(k.lower().strip()) for k in kws) + ")", re.I)


def classify_rules(item_title: str, text: str, published_at: str | None = None) -> EventClassification:
    factors_cfg = load_yaml("factors.yaml")["factors"]
    clean, flags = sanitize_untrusted(text)
    full = f"{item_title}. {clean}"
    low = full.lower()
    sents = _sentences(full)
    hits: list[FactorHit] = []
    for fid, f in factors_cfg.items():
        rx = _kw_re(f.get("keywords", []))
        matched = sorted({m.group(0).lower() for m in rx.finditer(low)})
        if not matched:
            continue
        ev = next((s for s in sents if rx.search(s)), None)
        direction = _direction(ev, rx) if ev else "unknown"
        in_title = bool(rx.search(item_title))
        conf = min(0.9, 0.35 + 0.15 * len(matched) + (0.25 if in_title else 0))
        hits.append(FactorHit(factor=fid, direction=direction, confidence=round(conf, 2), evidence=ev))
    hits.sort(key=lambda h: h.confidence, reverse=True)
    facts = [s for s in sents if not FORECAST.search(s)][:4]
    opinions = [s for s in sents if FORECAST.search(s)][:4]
    numbers = list(dict.fromkeys(NUMBER.findall(full)))[:8]
    importance = 2 + (1 if OFFICIAL_HINTS.search(full) else 0) + (1 if numbers else 0) + (1 if len(hits) >= 3 else 0)
    return EventClassification(
        summary=sents[0] if sents else item_title,
        event_type=_event_type(hits),
        factors=hits[:6],
        countries=[c for c in COUNTRIES if re.search(r"(?<![а-яё])" + c.lower()[:5], low)],
        facts=facts,
        forecasts_or_opinions=opinions,
        numbers=numbers,
        importance=min(5, importance),
        regions=extract_regions(full),
        industries=extract_industries(full),
        effective_dates=extract_effective_dates(full, published_at),
        security_flags=flags,
        classifier="rules",
    )


def _event_type(hits: list[FactorHit]) -> str:
    ids = {h.factor for h in hits[:3]}
    if "key_rate" in ids:
        return "monetary_policy"
    if ids & {"tax_regulation", "budget_policy"}:
        return "regulation_budget"
    if ids & {"sanctions_trade", "china_trade", "geopolitics"}:
        return "trade_geopolitics"
    if ids & {"commodity_oil", "commodity_metals", "gold", "fuel"}:
        return "commodities"
    if ids & {"inflation", "consumer_demand", "labor_market"}:
        return "macro_statistics"
    if ids & {"bond_market", "credit_conditions"}:
        return "financial_markets"
    return "industry"


SYSTEM_PROMPT = """Ты — аналитик новостей корпоративного банка. Классифицируй событие для дальнейшего анализа влияния на компании.
{notice}
Верни строго JSON:
{{"summary": "1-2 предложения, только факты",
 "event_type": "monetary_policy|macro_statistics|regulation_budget|trade_geopolitics|commodities|financial_markets|industry|other",
 "factors": [{{"factor": "<id из списка>", "direction": "up|down|stable|unknown", "confidence": 0..1, "evidence": "цитата из текста"}}],
 "countries": [], "facts": ["проверяемые факты из текста"], "forecasts_or_opinions": ["прогнозы, мнения, планы"],
 "numbers": ["ключевые числа с единицами"], "importance": 1..5,
 "regions": ["регионы РФ, если новость региональная"], "industries": ["коды классов ОКВЭД (2 цифры) отраслей, о которых новость"],
 "effective_dates": ["YYYY-MM-DD — даты вступления в силу / начала действия, если есть"]}}
direction — направление изменения самого фактора (например, ставка выросла = up). Используй только id факторов:
{factors}"""


def classification_messages(item: NewsItem) -> tuple[list[dict], list[str]]:
    factors_cfg = load_yaml("factors.yaml")["factors"]
    factor_list = "\n".join(f"- {k}: {v['title']}" for k, v in factors_cfg.items())
    clean, flags = sanitize_untrusted(item.text)
    title, _ = sanitize_untrusted(item.title)
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(notice=UNTRUSTED_NOTICE, factors=factor_list)},
        {"role": "user", "content": f"<news>\nЗаголовок: {title}\n{clean}\n</news>"},
    ], flags


def _merge_entities(cls: EventClassification, rules: EventClassification) -> EventClassification:
    """LLM дополняет, но не ломает сущности: регионы и даты — только валидные, отрасли — 2 цифры ОКВЭД."""
    known = set(REGION_PATTERNS.values())
    cls.regions = list(dict.fromkeys(rules.regions + [r for r in cls.regions if r in known]))[:5]
    cls.industries = list(dict.fromkeys(rules.industries + [i for i in cls.industries if re.fullmatch(r"\d{2}", str(i))]))[:5]
    dates = [d for d in cls.effective_dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(d))]
    cls.effective_dates = sorted(set(rules.effective_dates + dates))[:3]
    return cls


# Источники, где LLM-разбор оправдан даже без факторов по правилам: регулятор и запросы пользователя
ALWAYS_LLM_TRUST = {"official", "user"}


def classify(item: NewsItem, trust: str = "media") -> EventClassification:
    """Сначала дешёвые правила; LLM — только если правила нашли бизнес-факторы или источник официальный.

    Так фоновый сбор не тратит бюджет на новости вне повестки (их в лентах СМИ большинство).
    """
    rules = classify_rules(item.title, item.text, item.published_at)
    rules.trust = trust
    llm = get_llm()
    if not llm.enabled or (not rules.factors and trust not in ALWAYS_LLM_TRUST):
        return rules
    factors_cfg = load_yaml("factors.yaml")["factors"]
    messages, flags = classification_messages(item)
    try:
        data, res = llm.complete_json("classify_news", messages, ClassificationOut, max_tokens=800)
        # модель иногда повторяет фактор с разными направлениями — оставляем самый уверенный
        best: dict[str, dict] = {}
        for f in data.get("factors", []):
            if f.get("factor") in factors_cfg and (f["factor"] not in best or
                                                   f.get("confidence", 0) > best[f["factor"]].get("confidence", 0)):
                best[f["factor"]] = f
        data["factors"] = sorted(best.values(), key=lambda f: -f.get("confidence", 0))
        cls = EventClassification.model_validate({**data, "classifier": f"llm:{res.model}"})
        cls.security_flags = sorted(set(cls.security_flags) | set(flags))
        if not cls.factors:  # модель не нашла факторов — берём правила
            cls.factors = rules.factors
        cls.trust = trust
        return _merge_entities(cls, rules)
    except (LLMUnavailable, ValueError, KeyError) as e:
        log.info("Классификация через правила: %s", e)
        return rules
    except Exception as e:  # pydantic ValidationError и пр.
        log.warning("LLM-классификатор вернул некорректный ответ: %s", e)
        return rules
