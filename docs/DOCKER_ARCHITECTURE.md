# Docker Architecture Recommendations

## Security Improvements

### 1. Add non-root user to Python containers

**Dockerfile / Dockerfile.parser:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Security: Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install deps as root
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Change ownership
RUN chown -R appuser:appuser /app

# Switch to non-root
USER appuser

EXPOSE 8080
CMD ["python", "main.py"]
```

### 2. Network Segmentation

**docker-compose.yml:**
```yaml
networks:
  frontend:
    driver: bridge
  backend:
    driver: bridge
    internal: true  # No external access
  db:
    driver: bridge
    internal: true

services:
  nginx:
    networks:
      - frontend
      # No access to backend/db
  
  app:
    networks:
      - frontend
      - backend
      - db
  
  parser:
    networks:
      - backend
      - db
    # No access to frontend
  
  postgres:
    networks:
      - db
    # Only accessible via backend network
```

### 3. Resource Limits

```yaml
services:
  app:
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
        reservations:
          cpus: '0.25'
          memory: 128M
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=50m
  
  parser:
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 512M  # For media processing
        reservations:
          cpus: '0.1'
          memory: 256M
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=100m
```

### 4. Health Checks

```yaml
  app:
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
  
  parser:
    healthcheck:
      test: ["CMD-SHELL", "ps aux | grep -v grep | grep -q monitoring || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### 5. Security Options

```yaml
  app:
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE  # Only if binding to low port
```

## Size Optimizations

### Multi-stage build for smaller images:

```dockerfile
# Build stage
FROM python:3.11-slim as builder

WORKDIR /app
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.11-slim

# Copy only installed packages
COPY --from=builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH

# Or use distroless for ultimate minimalism
# FROM gcr.io/distroless/python3-debian12
```

## Complete Secure docker-compose.yml

```yaml
version: '3.8'

services:
  postgres:
    build:
      context: .
      dockerfile: Dockerfile.postgres
    container_name: postgres
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password
      POSTGRES_DB: postgres
    secrets:
      - postgres_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - db
    expose:
      - "5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
    restart: unless-stopped

  app:
    build: .
    container_name: app
    environment:
      DB_HOST: postgres
      DB_PORT: 5432
      DB_NAME: postgres
      DB_USER: postgres
      DB_PASSWORD_FILE: /run/secrets/postgres_password
      # Remove secrets from env, use files
    secrets:
      - postgres_password
      - bot_token
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - backend
      - db
    expose:
      - "8080"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=50m
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
    restart: unless-stopped

  parser:
    build:
      context: .
      dockerfile: Dockerfile.parser
    container_name: parser
    environment:
      DB_HOST: postgres
      DB_PORT: 5432
      DB_NAME: postgres
      DB_USER: postgres
      DB_PASSWORD_FILE: /run/secrets/postgres_password
      EVENTS_MEDIA_DIR: /media/events
    secrets:
      - postgres_password
      - bot_token
      - channel_id
    volumes:
      - events_media:/media/events
      - ./parser/session.session:/app/session.session:ro
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - backend
      - db
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=100m
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 512M
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    container_name: nginx
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./web:/usr/share/nginx/html:ro
      - events_media:/usr/share/nginx/html/assets/images/events:ro
    depends_on:
      - app
    networks:
      - frontend
      - backend
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE
    read_only: true
    tmpfs:
      - /var/cache/nginx:noexec,nosuid,size=50m
      - /var/run:noexec,nosuid,size=10m
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 128M
    restart: unless-stopped

networks:
  frontend:
    driver: bridge
  backend:
    driver: bridge
  db:
    driver: bridge
    internal: true  # No external access to DB network

volumes:
  postgres_data:
    driver: local
  events_media:
    driver: local

secrets:
  postgres_password:
    file: ./secrets/postgres_password.txt
  bot_token:
    file: ./secrets/bot_token.txt
  channel_id:
    file: ./secrets/channel_id.txt
```

## Implementation Priority

1. **High (Critical Security):**
   - Add non-root user to Python containers
   - Implement network segmentation
   - Add read_only filesystem

2. **Medium (Stability):**
   - Add health checks for app/parser
   - Add resource limits
   - Add restart policies

3. **Low (Optimization):**
   - Migrate to Docker secrets
   - Multi-stage builds
   - Use distroless images

## Quick Wins (apply now)

```bash
# Create secrets directory
mkdir -p secrets
echo "postgres" > secrets/postgres_password.txt
echo "8216743620:AAH0K9zFuBffRvZ4Ma25JHBbrs8Sy1jxlcA" > secrets/bot_token.txt
echo "-1002050105527" > secrets/channel_id.txt
chmod 600 secrets/*
```

Then add `secrets` section to docker-compose.yml and update env vars to use `*_FILE` pattern.
