FROM python:3.12-slim

ARG GIT_COMMIT=unknown
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TEBYPASS_PORT=9384
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
COPY app/ ./app/
COPY THIRD_PARTY_NOTICES.md ./
RUN printf '%s\n' "$GIT_COMMIT" > /app/BUILD_COMMIT
EXPOSE 9384
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9384/healthz',timeout=3)"
ENTRYPOINT ["python", "-m", "app.main"]
