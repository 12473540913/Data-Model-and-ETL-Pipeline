install:
	python -m pip install -r requirements.lock
	python -m pip install --no-deps -e .

db:
	docker compose up -d db

test:
	python -m pytest -q

lint:
	python -m ruff check src tests migrations

api:
	uvicorn genomic_platform.api.main:app --reload --port 8000

soap:
	python -m genomic_platform.soap.server

seed:
	python -m genomic_platform.cli seed
