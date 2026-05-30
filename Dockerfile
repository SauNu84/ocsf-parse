# Single-stage image. The package is small (<5 MB) and the heaviest deps
# (pyarrow, fastapi) all wheel-build fine on slim Python; no need for a
# multi-stage build to shave bytes.

FROM python:3.12-slim

# uvicorn + httpx + pyarrow benefit from a few system libs being present.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package + reference data first so layer caches well on iteration.
COPY pyproject.toml README.md ./
COPY src/  ./src/
COPY mappings/  ./mappings/
COPY samples/   ./samples/
COPY catalog.json ./catalog.json
COPY ocsf-schema/ ./ocsf-schema/

# Install with the full feature set — web UI, Parquet sink, orjson fast-path.
# `[fast]` brings orjson (5-10× JSON), `[parquet]` brings pyarrow, `[web]`
# brings FastAPI + uvicorn + jinja2.
RUN pip install --no-cache-dir -e '.[web,parquet,fast]'

# Expose the default web UI port. Bind to 0.0.0.0 inside the container so
# `docker run -p 8000:8000` works without extra flags. (Locally the CLI
# still defaults to 127.0.0.1 — only the container override is wider.)
EXPOSE 8000
ENV OCSF_HOST=0.0.0.0
ENV OCSF_PORT=8000

# tini handles signal forwarding so `docker stop` works cleanly.
ENTRYPOINT ["/usr/bin/tini", "--", "ocsf-mapper"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
