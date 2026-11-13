# proofquery container: warehouse built from the bundled CSV at image-build
# time, so the API container is query-ready the moment uvicorn answers.
FROM python:3.12-slim

# curl is needed for HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the package first (better layer caching: src changes rarely invalidate deps).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[serve]"

# The dataset is the fetch query's output (ADR-004); ship those exact bytes and
# pin where the app looks for them (installed package can't infer REPO_ROOT).
COPY data/raw/cfpb_complaints.csv ./data/raw/cfpb_complaints.csv
COPY dashboards ./dashboards
ENV PROOFQUERY_DATA_DIR=/app/data \
    PROOFQUERY_DASHBOARD=/app/dashboards/dashboard.html

# Fail the build, not the runtime, if the warehouse doesn't reproduce.
RUN python -m proofquery.warehouse

EXPOSE 8000
CMD ["uvicorn", "proofquery.server:app", "--host", "0.0.0.0", "--port", "8000"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
