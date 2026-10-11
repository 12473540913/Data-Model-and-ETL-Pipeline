FROM python:3.11-slim

WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-deps .
RUN useradd --create-home genomic
USER genomic
EXPOSE 8000
CMD ["uvicorn", "genomic_platform.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
