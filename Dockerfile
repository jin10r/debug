FROM python:3.11-slim

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

# Set ownership and switch to non-root user
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Health check for app process
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ps aux | grep -v grep | grep -q main.py || exit 1

CMD ["python", "main.py"]
