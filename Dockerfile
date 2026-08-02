FROM python:3.10-slim

ARG INSTALL_LTX=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p virector && touch virector/__init__.py README.md && \
    python -m pip install --upgrade pip && \
    python -m pip install ".[dev]" && \
    if [ "$INSTALL_LTX" = "1" ]; then python -m pip install ".[ltx]"; fi && \
    rm -rf virector

COPY README.md ./
COPY virector ./virector
COPY tests ./tests

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["sh", "-c", "uvicorn virector.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
