install:
	pip install -e ".[dev,data,orchestration]"

db:
	docker compose up -d db

test:
	pytest -q

lint:
	ruff check src tests

api:
	uvicorn genomic_platform.api.main:app --reload --port 8000

soap:
	python -m genomic_platform.soap.server

seed:
	python -m genomic_platform.cli seed
