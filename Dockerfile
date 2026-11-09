FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TEBYPASS_PORT=9384
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
COPY app/ ./app/
COPY THIRD_PARTY_NOTICES.md LICENSE ./
ARG APP_VERSION=0.2.0
ARG GIT_TAG=""
RUN printf '%s\n' "$APP_VERSION" > /app/BUILD_VERSION && printf '%s\n' "$GIT_TAG" > /app/BUILD_TAG
ARG GIT_COMMIT=unknown
RUN printf '%s\n' "$GIT_COMMIT" > /app/BUILD_COMMIT
EXPOSE 9384
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9384/healthz',timeout=3)"
ENTRYPOINT ["python", "-m", "app.main"]
