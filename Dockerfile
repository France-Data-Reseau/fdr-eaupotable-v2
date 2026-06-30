FROM python:3.12-slim

# Install any system dependencies if needed (psycopg-binary usually doesn't need much)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy pyproject.toml and README.md (required by hatchling build backend)
COPY pyproject.toml README.md /app/

# Setup a virtual environment and install dependencies
# We use uv sync to install the project dependencies
RUN uv sync --no-dev

# Copy the rest of the application
COPY . /app/

# Re-run sync to install the project itself if needed, or rely on PYTHONPATH
RUN uv sync --no-dev

# Ensure the virtualenv is in PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src:${PYTHONPATH}"

# Default command for production using Gunicorn WSGI server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "fdr_etl.web.app:create_app()"]
