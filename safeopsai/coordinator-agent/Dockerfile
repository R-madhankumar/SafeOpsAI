FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package (includes config.yml) and the migration SQL it needs at runtime
COPY coordinator_agent/ ./coordinator_agent/

# The Step 6 migration is mounted at /app/database/ so db.py can find it via
# its Path(__file__) relative resolution (/app/database/migrate_step6.sql).
RUN mkdir -p /app/database

RUN useradd -m agentuser && chown -R agentuser /app
USER agentuser

EXPOSE 8004

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8004/health || exit 1

CMD ["python", "-m", "coordinator_agent.main"]