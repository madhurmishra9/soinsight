# ── Stage 1: install Python dependencies ─────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /build

COPY pyproject.toml .
# Stub packages so setuptools can resolve without full source present.
RUN mkdir -p app services routers eval \
    && touch app/__init__.py services/__init__.py routers/__init__.py eval/__init__.py
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN groupadd -r soinsight && useradd -r -g soinsight soinsight

WORKDIR /app

# Installed site-packages + entry-point scripts from the builder.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Application source (on sys.path before site-packages stubs — WORKDIR wins).
COPY app/ app/
COPY services/ services/
COPY routers/ routers/
COPY eval/ eval/

RUN mkdir -p /app/data && chown -R soinsight:soinsight /app

USER soinsight

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
