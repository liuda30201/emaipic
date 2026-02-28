FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY services/ai_dispatcher/requirements.txt /app/services/ai_dispatcher/requirements.txt

RUN pip install --no-cache-dir -r /app/services/ai_dispatcher/requirements.txt

COPY . /app

CMD ["uvicorn", "services.ai_dispatcher.app.main:app", "--host", "0.0.0.0", "--port", "3005"]
