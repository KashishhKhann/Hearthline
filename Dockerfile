FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as a non-root user; data/ must be writable for resident reports.
RUN useradd --create-home --uid 1000 hearthline \
    && mkdir -p /app/data \
    && chown -R hearthline:hearthline /app/data
USER hearthline

# 8501 = Streamlit UI (default command), 8000 = FastAPI (see docker-compose.yml).
# When deployed, set HEARTHLINE_API_PUBLIC_URL to the public URL of the API.
EXPOSE 8501 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
