FROM python:3.15-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    PATH="/app/.venv/bin:${PATH}" \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN python -m pip install --upgrade pip uv \
    && uv sync --frozen --no-dev \
    && apt-get purge -y git \
    && apt-get autoremove -y --purge \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

RUN /app/.venv/bin/python -m scripts.publish_digest_before_read --help

CMD ["/app/.venv/bin/python", "-m", "gunicorn", "--bind", ":8080", "main:app"]
