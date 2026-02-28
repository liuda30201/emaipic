FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY services/image_preprocessor/requirements.txt /app/services/image_preprocessor/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /app/services/image_preprocessor/requirements.txt

COPY . /app

CMD ["uvicorn", "services.image_preprocessor.app.main:app", "--host", "0.0.0.0", "--port", "3004"]
