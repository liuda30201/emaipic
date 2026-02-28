FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY services/index_export/requirements.txt /app/services/index_export/requirements.txt

RUN pip install --no-cache-dir -r /app/services/index_export/requirements.txt

COPY . /app

CMD ["uvicorn", "services.index_export.app.main:app", "--host", "0.0.0.0", "--port", "3007"]
