FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY services/mail_ingestor/requirements.txt /app/services/mail_ingestor/requirements.txt

RUN pip install --no-cache-dir -r /app/services/mail_ingestor/requirements.txt

COPY . /app

CMD ["uvicorn", "services.mail_ingestor.app.main:app", "--host", "0.0.0.0", "--port", "3002"]
