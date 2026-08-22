FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the package (includes config.yml) and the migration SQL it needs at runtime
COPY root_cause_agent/ ./root_cause_agent/

# The migration SQL lives two levels up in the repo; in the container
# it is mounted at /app/database/ so db.py can find it via its Path(__file__)
# relative resolution (which resolves to /app/database/migrate_step4.sql).
# Create the dir here so the image is self-contained even if the mount is absent.
RUN mkdir -p /app/database

# The service topology is shared with the incident-agent and mounted at runtime
# at /app/root_cause_agent/topology.yml (the default TOPOLOGY_FILE location).

RUN useradd -m agentuser && chown -R agentuser /app
USER agentuser

EXPOSE 8002

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

CMD ["python", "-m", "root_cause_agent.main"]