# -------------------------------
# Build stage: React client build
# -------------------------------
FROM node:20-alpine AS client-build
WORKDIR /app
# Copy only package files first for better layer caching
COPY client/package.json client/package-lock.json* ./client/
RUN cd client && npm ci --legacy-peer-deps --silent
# Copy the remainder of the client source and build
COPY client ./client
RUN cd client && npm run build

# -------------------------------
# Runtime stage: Python backend + built client
# -------------------------------
FROM python:3.11-slim AS runtime

# Install system dependencies (if any Socket.IO / other libs require extras)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend & shared application code
COPY backend ./backend
COPY app ./app
COPY app_runner.py ./app_runner.py
COPY version_info.txt ./version_info.txt
COPY assets ./assets

# Copy the built React client from the previous stage
COPY --from=client-build /app/client/build ./client/build

# Create user home data directory and default config
RUN mkdir -p "$HOME/mcpclientdata" "$HOME/.mcpclient" \
    && echo "{\"data_dir\": \"$HOME/mcpclientdata\"}" > "$HOME/.mcpclient/mcpclient.conf"

# Expose the application port (matches uvicorn)
EXPOSE 3001

# Start the FastAPI + Socket.IO server using uvicorn
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0"]
