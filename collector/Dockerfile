# WHM Analytics Collector
# Multi-stage build for smaller image

FROM python:3.12-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# Production image
FROM python:3.12-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r whm && useradd -r -g whm whm

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Copy application
COPY gunicorn.conf.py .
COPY app/ ./app/

# Create directories
RUN mkdir -p /opt/whm-analytics/config \
             /opt/whm-analytics/data \
             /opt/whm-analytics/logs \
    && chown -R whm:whm /opt/whm-analytics

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WHM_HOST=0.0.0.0 \
    WHM_PORT=9100

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9100/health || exit 1

# Switch to non-root user
USER whm

# Expose port
EXPOSE 9100

# Run with Gunicorn
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
