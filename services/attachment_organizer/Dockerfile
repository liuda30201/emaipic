FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY services/attachment_organizer/requirements.txt /app/services/attachment_organizer/requirements.txt

RUN pip install --no-cache-dir -r /app/services/attachment_organizer/requirements.txt

COPY . /app

CMD ["uvicorn", "services.attachment_organizer.app.main:app", "--host", "0.0.0.0", "--port", "3003"]
