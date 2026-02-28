FROM python:3.11-slim

WORKDIR /app

COPY frontend /app/frontend

CMD ["python", "-m", "http.server", "3000", "--directory", "/app/frontend"]
