FROM python:3.11-slim-bookworm

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY api/ api/

RUN pip install --no-cache-dir -e .

# Persistent volume for SQLite will be mounted here
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/app/data/token_manager.db

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
