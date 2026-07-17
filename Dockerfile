# -------------------------------
# Build stage: React client build
# -------------------------------
FROM node:24-bookworm-slim AS client-build
WORKDIR /app
# Copy only package files first for better layer caching
COPY client/package.json client/package-lock.json* ./client/
RUN cd client \
    && npm config set maxsockets 1 \
    && npm ci --legacy-peer-deps \
        --fetch-retries=5 \
        --fetch-retry-mintimeout=20000 \
        --fetch-retry-maxtimeout=120000
# Copy the remainder of the client source and build
COPY client ./client
RUN cd client && npm run build

# -------------------------------
# Shared runtime stage: Python backend
# -------------------------------
FROM python:3.11-slim AS runtime-base

# Create working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend & shared application code
COPY backend ./backend
COPY app ./app
COPY version_info.txt ./version_info.txt

RUN groupadd --system mcpclient \
    && useradd --system --gid mcpclient --create-home mcpclient \
    && mkdir -p /data \
    && chown -R mcpclient:mcpclient /app /data

ENV MCPCLIENT_HEADLESS=1 \
    MCPCLIENT_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

VOLUME ["/data"]
USER mcpclient

# Expose backend HTTP port
EXPOSE 3001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3001/healthz', timeout=3).read()"]

# Launch FastAPI + Socket.IO server directly (no GUI)
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "3001"]

# Optional offline/diagnostic target. Build the client on the host first, then:
# docker build --target runtime-prebuilt -t mcp-client-foundry .
FROM runtime-base AS runtime-prebuilt
COPY --chown=mcpclient:mcpclient client/build ./client/build

# Default release target builds the React client reproducibly inside Docker.
FROM runtime-base AS runtime
COPY --from=client-build --chown=mcpclient:mcpclient /app/client/build ./client/build
