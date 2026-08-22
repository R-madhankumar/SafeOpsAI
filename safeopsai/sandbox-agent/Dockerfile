FROM python:3.12-slim

WORKDIR /app

# Install curl for HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY sandbox_agent /app/sandbox_agent

# Create empty database directory so path resolution works if migration file is mounted
RUN mkdir -p /app/database

ENV PYTHONUNBUFFERED=1
ENV PORT=8005

EXPOSE 8005

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

CMD ["python", "-m", "sandbox_agent.main"]
