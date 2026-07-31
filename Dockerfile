FROM python:3.10-slim

ARG INSTALL_LTX=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY virector ./virector
COPY tests ./tests

RUN python -m pip install --upgrade pip && \
    python -m pip install ".[dev]" && \
    if [ "$INSTALL_LTX" = "1" ]; then python -m pip install ".[ltx]"; fi

EXPOSE 8000

CMD ["uvicorn", "virector.main:app", "--host", "0.0.0.0", "--port", "8000"]
