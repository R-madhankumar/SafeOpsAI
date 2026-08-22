FROM python:3.12-slim

WORKDIR /app

# Install curl for HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY remediation_agent /app/remediation_agent

# Create empty database directory so path resolution works if migration file is mounted
RUN mkdir -p /app/database

ENV PYTHONUNBUFFERED=1
ENV PORT=8006

EXPOSE 8006

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8006/health || exit 1

CMD ["python", "-m", "remediation_agent.main"]
