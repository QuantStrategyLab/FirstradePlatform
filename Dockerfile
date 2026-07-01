FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY constraints.txt ./
RUN python -m pip install --upgrade pip \
    && grep -vE "^\s*(#|$)|git\+" requirements.txt | xargs -r python -m pip install -c constraints.txt \
    && grep "git+" requirements.txt | while IFS= read -r pkg; do \
        [ -n "$pkg" ] && python -m pip install --no-deps "$pkg"; \
    done \
    && apt-get purge -y git \
    && apt-get autoremove -y --purge \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["gunicorn", "--bind", ":8080", "main:app"]
