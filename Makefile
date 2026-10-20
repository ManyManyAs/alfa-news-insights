# Основные команды проекта. Windows: используйте WSL или команды из README напрямую.
PY ?= python
BACK = backend

.PHONY: help install run test lint fmt eval sample docker docker-test snapshot warmup ingest

help:            ## Показать команды
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

install:         ## Установить зависимости (Python 3.12)
	cd $(BACK) && $(PY) -m pip install -r requirements.lock

run:             ## Запустить API + UI на http://localhost:8000
	cd $(BACK) && $(PY) -m uvicorn app.main:app --reload --port 8000

test:            ## Тесты
	cd $(BACK) && $(PY) -m pytest -q

lint:            ## Линтер
	cd $(BACK) && ruff check .

fmt:             ## Автоисправление стиля
	cd $(BACK) && ruff check . --fix

eval:            ## Офлайн-метрики качества
	cd $(BACK) && $(PY) scripts/evaluate.py --insights

sample:          ## Перегенерировать синтетические данные
	cd $(BACK) && $(PY) scripts/make_sample_data.py

warmup:          ## Построить инсайты для демо-пользователей
	cd $(BACK) && $(PY) scripts/manage.py warmup

snapshot:        ## Сохранить результаты LLM в data/snapshot (после прогона в live)
	cd $(BACK) && $(PY) scripts/manage.py snapshot-export

ingest:          ## Собрать новости из RSS
	cd $(BACK) && $(PY) scripts/manage.py ingest

docker:          ## Собрать и запустить в Docker
	docker compose up --build

docker-test:     ## Тесты в Docker
	docker compose --profile test run --rm tests
