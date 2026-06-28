# TRIPWIRE read-only dashboard — runs app.py with no API key and no network.
# Build:  docker build -t tripwire-dashboard .
# Run:    docker run -p 8501:8501 tripwire-dashboard
FROM python:3.11-slim

WORKDIR /app

# Quieter, reproducible Python + no Streamlit telemetry / email prompt
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Install only the dashboard's dependencies (streamlit, plotly, pandas) so the
# image stays small — the full requirements.txt (anthropic, scikit-learn, etc.)
# is only needed to re-run the research pipeline, not the read-only dashboard.
COPY requirements-dashboard.txt ./
RUN pip install --upgrade pip && pip install -r requirements-dashboard.txt

# Copy only what the read-only dashboard actually needs at runtime:
#   app.py            — the dashboard
#   README.md         — read by the Overview section (research-question text)
#   data/processed/   — the committed result files the dashboard visualizes
# (scripts/, src/, tests/, notebooks/, data/raw/ are excluded via .dockerignore)
COPY app.py README.md ./
COPY data/processed/ ./data/processed/

EXPOSE 8501

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
