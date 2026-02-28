FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY services/orchestrator/requirements.txt /app/services/orchestrator/requirements.txt

RUN pip install --no-cache-dir -r /app/services/orchestrator/requirements.txt

COPY . /app

CMD ["uvicorn", "services.orchestrator.app.main:app", "--host", "0.0.0.0", "--port", "3001"]
