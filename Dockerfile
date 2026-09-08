# VIBBO MCP server, HTTP transport, for Google Cloud Run.
#
# The image has no third-party packages: the server is standard library only.
# The base image is slim rather than alpine because the official Python
# alpine images build slower and gain nothing here.

FROM python:3.13-slim

# Cloud Run terminates the container on any unhandled signal, so Python must
# not buffer its logs or they are lost on shutdown.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# The application, minus anything the server does not need at runtime.
COPY src/ ./src/
COPY data/schema.sql data/seed.py ./data/
COPY data/policies/ ./data/policies/

# The database is built into the image at build time, not shipped in the repo.
# Same seed, same reference date, so the container holds exactly the rows a
# local clone does.
RUN python data/seed.py

# Cloud Run injects PORT. 8080 is its default and the local fallback.
ENV PORT=8080
EXPOSE 8080

# Run as a non-root user. Cloud Run does not require it; it is simply correct.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "src", "--http", "--host", "0.0.0.0", "--log-level", "INFO"]
