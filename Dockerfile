FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime dependencies only (no pytest in the image). Copied first so Docker
# reuses this layer until requirements.txt actually changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Run as an unprivileged user. /data is where the SQLite incident log lives;
# mount a volume there to keep incidents across container restarts.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sentinel \
 && mkdir /data \
 && chown sentinel /data
USER sentinel
ENV SENTINEL_DB=/data/incidents.db
VOLUME /data

EXPOSE 8000

# No curl in the slim image, so use Python. A non-2xx answer or no answer -> non-zero exit.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
