# BUD (بُعد) — FastAPI app + dashboard. Migrations run via the compose `migrate` service.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Addresses of the reverse proxy (nginx, load balancer) allowed to set X-Forwarded-For.
    # Without this every request looks like it comes from the proxy, so per-IP limits
    # (portal requests, login lockout) would apply to all customers together.
    FORWARDED_ALLOW_IPS=127.0.0.1

# libpq for asyncpg's C extension build; removed from the final layer isn't
# worth the multi-stage complexity at this project's size.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir . \
    && useradd --system --uid 10001 --home /app bud \
    && mkdir -p /app/app/static/uploads \
    && chown -R bud /app/app/static/uploads

# Never run the web server as root.
USER bud
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]
