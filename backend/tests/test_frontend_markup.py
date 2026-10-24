"""Разметка интерфейса: экраны (section.view) лежат прямо в <main> и не вложены друг в друга.

Вложенный экран не виден (родитель скрыт) — так однажды «пропали» календарь и разделы банка после слияния веток.
"""
from html.parser import HTMLParser
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "frontend" / "index.html"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "path", "circle",
        "rect", "polyline", "line", "ellipse", "polygon"}


class Views(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, str]] = []
        self.views: dict[str, list[str]] = {}
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and "view" in (a.get("class") or "").split():
            self.views[a.get("id", "?")] = [t for t, _ in self.stack]
        if tag not in VOID:
            self.stack.append((tag, a.get("id", "")))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append(f"лишний или несбалансированный </{tag}> (открыт {self.stack[-1] if self.stack else '—'})")
            while self.stack and self.stack[-1][0] != tag:
                self.stack.pop()
        if self.stack:
            self.stack.pop()


def test_views_are_direct_children_of_main():
    p = Views()
    p.feed(INDEX.read_text(encoding="utf-8"))
    assert not p.errors, p.errors[:5]
    assert {"view-home", "view-radar", "view-digest", "view-bank"} <= set(p.views)
    for vid, ancestors in p.views.items():
        assert "section" not in ancestors, f"экран {vid} вложен в другой экран"
        assert ancestors[-1] == "main", f"экран {vid} не лежит прямо в <main>: {ancestors[-3:]}"


def test_every_static_file_in_css_and_html_exists():
    """Шрифты и файлы, на которые ссылаются стили и разметка, лежат в репозитории (иначе браузер молча берёт запасной)."""
    import re

    front = INDEX.parent
    css = (front / "styles.css").read_text(encoding="utf-8") + INDEX.read_text(encoding="utf-8")
    refs = set(re.findall(r"/static/([\w./-]+\.(?:woff2?|svg|png|webmanifest|js|css))", css))
    missing = sorted(r for r in refs if not (front / r).exists())
    assert not missing, f"нет файлов: {missing}"
