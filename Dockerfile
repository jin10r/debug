FROM python:3.11.10-slim-bookworm

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Install system dependencies as root with BuildKit caching
RUN --mount=type=cache,target=/var/cache/apt,id=app-apt \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies with pip caching
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY core/ ./core/
COPY main.py .

# Create media directory with correct ownership
RUN mkdir -p /app/media/events && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

# Healthcheck определён в docker-compose.yml — single source of truth.
# Дубликат HEALTHCHECK здесь был source of drift между compose и Dockerfile.

CMD ["python", "main.py"]
