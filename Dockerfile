FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    AGENTSEARCHER_HOST=0.0.0.0

# Chromium and its system libraries first: the heaviest layer, rebuilt only when this line changes
RUN pip install "patchright>=1.50" \
    && python -m patchright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY src ./src
RUN pip install . \
    && agentsearcher fetch-model \
    && useradd --create-home app \
    && chown -R app /opt/hf

USER app
EXPOSE 8484
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/health')"
CMD ["agentsearcher", "serve"]
