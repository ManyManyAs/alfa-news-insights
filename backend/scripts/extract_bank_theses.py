"""OCR аналитики Альфа-Банка (сканы PDF) + извлечение тезисов позиции банка.

1. Каждая страница PDF рендерится в PNG (pdftoppm) и распознаётся Qwen3-VL-30B-A3B-Instruct.
   Результат: data/knowledge/docs/<doc_id>.md с разделами «## Стр. N» — индексируется BM25.
2. (опционально, --theses) Kimi-K2.6 извлекает тезисы {factor, direction, horizon, thesis, quote, page}
   в data/knowledge/bank_theses.auto.json — для ручной проверки и переноса в bank_theses.json.

Запуск (нужен LLM_MODE=live и ключ):
  uv run python scripts/extract_bank_theses.py --pdf-dir ../data/raw/bank_pdfs [--theses]
Стоимость: ~64 страницы × ~1.5k токенов ≈ десятки рублей — запускать один раз, результат кэшируется.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings, load_yaml
from app.llm.client import get_llm

OCR_PROMPT = ("Распознай страницу аналитического отчёта банка. Верни текст в Markdown, сохраняя заголовки и абзацы. "
              "Таблицы — в Markdown-таблицы. Для графиков кратко опиши, что показано и ключевые значения. Ничего не добавляй от себя.")

THESES_PROMPT = """Из текста аналитического материала Альфа-Банка извлеки ключевые тезисы позиции банка (прогнозы, оценки, ожидания).
Верни JSON: {{"theses": [{{"factor": "<id>", "direction": "up|down|stable", "horizon": "...", "thesis": "1-2 предложения",
"quote": "дословная короткая цитата", "page": N}}]}}. Факторы: {factors}. Только то, что явно есть в тексте."""


def doc_id_for(pdf: Path, known: dict) -> str:
    for d in known.values():
        if Path(d["file"]).stem.lower()[:12] == pdf.stem.lower()[:12]:
            return d["doc_id"]
    return pdf.stem


def ocr_pdf(pdf: Path, out_md: Path) -> None:
    llm = get_llm()
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "110", "-png", str(pdf), f"{tmp}/p"], check=True)
        pages = sorted(Path(tmp).glob("p-*.png"))
        parts = []
        for i, png in enumerate(pages, 1):
            b64 = base64.b64encode(png.read_bytes()).decode()
            msg = [{"role": "user", "content": [
                {"type": "text", "text": OCR_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}]
            res = llm.complete("ocr_page", msg, max_tokens=2500, temperature=0)
            parts.append(f"## Стр. {i}\n\n{res.text.strip()}\n")
            print(f"  {pdf.name}: стр. {i}/{len(pages)} ({res.tokens_in}+{res.tokens_out} ток.)")
    out_md.write_text("\n".join(parts), encoding="utf-8")


def extract_theses(md: Path, doc_id: str) -> list[dict]:
    llm = get_llm()
    factors = ", ".join(load_yaml("factors.yaml")["factors"].keys())
    msg = [{"role": "system", "content": THESES_PROMPT.format(factors=factors)},
           {"role": "user", "content": f"<doc>\n{md.read_text(encoding='utf-8')[:30000]}\n</doc>"}]
    res = llm.complete("summarize_doc", msg, json_mode=True, max_tokens=2500)
    return [{**t, "doc_id": doc_id} for t in res.json().get("theses", [])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default=str(get_settings().raw_dir / "bank_pdfs"))
    ap.add_argument("--theses", action="store_true")
    args = ap.parse_args()
    if not get_llm().live:
        sys.exit("Нужен LLM_MODE=live и LLM_API_KEY")
    kdir = get_settings().knowledge_dir
    known = {d["doc_id"]: d for d in json.loads((kdir / "bank_theses.json").read_text(encoding="utf-8"))["documents"]}
    (kdir / "docs").mkdir(parents=True, exist_ok=True)
    auto = []
    for pdf in sorted(Path(args.pdf_dir).glob("*.pdf")):
        did = doc_id_for(pdf, known)
        out = kdir / "docs" / f"{did}.md"
        if not out.exists():
            print(f"OCR {pdf.name} -> {out.name}")
            ocr_pdf(pdf, out)
        if args.theses:
            auto += extract_theses(out, did)
    if args.theses:
        (kdir / "bank_theses.auto.json").write_text(json.dumps({"theses": auto}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Тезисов извлечено: {len(auto)} -> bank_theses.auto.json (проверьте вручную)")


if __name__ == "__main__":
    main()
