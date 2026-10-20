"""Адаптеры источников новостей. Новый тип источника = новый класс + регистрация в ADAPTERS."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from app.config import REPO_ROOT, load_yaml
from app.news.models import NewsItem

log = logging.getLogger(__name__)
_tag_re = re.compile(r"<[^>]+>")
UA = {"User-Agent": "alfa-news-insights-prototype/0.1 (hackathon; contact: team)"}


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_tag_re.sub(" ", s or ""))).strip()


def _id(source: str, key: str) -> str:
    return f"{source}-{hashlib.sha1(key.encode()).hexdigest()[:12]}"


class Source:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.id = cfg["id"]

    def fetch(self) -> list[NewsItem]:  # pragma: no cover - интерфейс
        raise NotImplementedError


class RSSSource(Source):
    def fetch(self) -> list[NewsItem]:
        with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
            r = c.get(self.cfg["url"])
            r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for it in root.iter("item"):
            title = strip_html(it.findtext("title") or "")
            link = (it.findtext("link") or "").strip() or None
            desc = strip_html(it.findtext("description") or "")
            full = it.find("{http://www.rbc.ru}full-text")
            if full is None:
                full = it.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            text = strip_html(full.text) if full is not None and full.text else desc
            pub = it.findtext("pubDate")
            try:
                published = parsedate_to_datetime(pub).isoformat() if pub else None
            except (TypeError, ValueError):
                published = None
            if title:
                items.append(NewsItem(id=_id(self.id, link or title), source=self.id, source_title=self.cfg.get("title"),
                                      url=link, title=title, text=text[:4000], published_at=published))
        return items


class JsonlSource(Source):
    def fetch(self) -> list[NewsItem]:
        path = Path(self.cfg["url"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(NewsItem.model_validate(json.loads(line)))
        return out


ADAPTERS = {"rss": RSSSource, "jsonl": JsonlSource}


def enabled_sources(only: list[str] | None = None) -> list[Source]:
    cfg = load_yaml("sources.yaml")
    res = []
    for s in cfg["sources"]:
        if not s.get("enabled", True) or (only and s["id"] not in only):
            continue
        cls = ADAPTERS.get(s["type"])
        if cls:
            res.append(cls(s))
    return res


def fetch_url(url: str) -> NewsItem:
    """Загрузка произвольной статьи по ссылке (для проверки на «невиданных» новостях)."""
    if not re.match(r"^https?://", url):
        raise ValueError("Допустимы только http(s) ссылки")
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
    page = r.text
    title_m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    page = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    paragraphs = [strip_html(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I)]
    text = " ".join(p for p in paragraphs if len(p) > 40)[:6000]
    title = strip_html(title_m.group(1)) if title_m else url
    return NewsItem(id=_id("url", url), source="user_url", source_title="Ссылка пользователя", url=url,
                    title=title, text=text or title)
