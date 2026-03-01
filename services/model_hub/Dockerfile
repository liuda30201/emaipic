FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY services/model_hub/requirements.txt /app/services/model_hub/requirements.txt

RUN pip install --no-cache-dir -r /app/services/model_hub/requirements.txt

COPY . /app

CMD ["uvicorn", "services.model_hub.app.main:app", "--host", "0.0.0.0", "--port", "3008"]
