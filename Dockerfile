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
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/ready', timeout=8).close()"
CMD ["uvicorn", "genomic_platform.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
