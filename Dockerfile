# Hosted demo image (Hugging Face Spaces, Render). Synthetic data only:
# DEMO_MODE isolates it from real data, credentials, OAuth, email and outbound HTTP.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEMO_MODE=true \
    DEMO_DATA_DIR=/tmp/career-agent-demo \
    PORT=7860 \
    APP_ORIGIN=http://localhost:7860

RUN useradd --create-home --uid 1000 app
WORKDIR /home/app/career-agent

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the application code; .env, data/ and uploads are never copied (see .dockerignore).
COPY --chown=app:app app ./app
COPY --chown=app:app check_config.py LICENSE ./

USER app
EXPOSE 7860
# On a hosted Space, set APP_ORIGIN to its public URL (e.g. https://<user>-<space>.hf.space).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
