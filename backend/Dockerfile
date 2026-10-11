FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
RUN useradd --create-home --uid 10001 appuser
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY --chown=appuser:appuser . .
USER appuser
EXPOSE 8000
