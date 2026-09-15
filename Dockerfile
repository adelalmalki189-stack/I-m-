# Single image, two roles selected at `docker run`/compose time via CMD override:
# the FastAPI app + dashboard (default CMD), or the live Scanner Loop
# (app/scripts/run_live_scanner.py) — see docker-compose.yml. Same image either
# way so there is never a version mismatch between the two processes.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# libpq for asyncpg's C extension build; removed from the final layer isn't
# worth the multi-stage complexity at this project's size.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
