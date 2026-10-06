# Optional application container. Dynamic test executor is NOT enabled inside it.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY repopilot ./repopilot
RUN python -m pip install --no-cache-dir '.[ai]' && useradd --uid 10001 --create-home studio && mkdir /data && chown studio:studio /data
USER studio
ENV STUDIO_DATA_DIR=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "repopilot.api:app", "--host", "0.0.0.0", "--port", "8000"]
