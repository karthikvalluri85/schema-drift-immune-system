# SDIS API + dashboard image (Paperclip itself runs via `npx paperclipai run`; see digitalocean/).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 sdis
WORKDIR /app
COPY --chown=sdis:sdis . /app
RUN pip install --no-cache-dir -e ".[api,dashboard]"
USER sdis
ENV SDIS_REPO_DIR=/app PYTHONUNBUFFERED=1
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1
CMD ["uvicorn", "sdis.api:app", "--host", "0.0.0.0", "--port", "8000"]
