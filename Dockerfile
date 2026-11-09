FROM python:3.12-slim

ARG APP_VERSION=0.1.1
ARG GIT_COMMIT=unknown

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TEBYPASS_PORT=9384

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app/ ./app/

EXPOSE 9384

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:9384/api/v1/status || exit 1

ENTRYPOINT ["python", "-m", "app.main"]
