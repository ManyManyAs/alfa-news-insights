FROM python:3.12.8-slim

# poppler-utils — рендер страниц PDF для OCR аналитики банка (scripts/extract_bank_theses.py)
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY backend/ ./
COPY config/ /app/config/
COPY frontend/ /app/frontend/
COPY data/knowledge/ /app/data/knowledge/
COPY data/sample/ /app/data/sample/
COPY data/case/ /app/data/case/
COPY data/snapshot/ /app/data/snapshot/
COPY data/eval/ /app/data/eval/

ENV PYTHONUNBUFFERED=1 \
    LLM_MODE=auto
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
