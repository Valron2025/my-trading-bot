FROM python:3.11-slim

# Установка CA-сертификатов
RUN apt-get update && \
    apt-get install -y ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Запуск
CMD ["gunicorn", "web_server:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "1", "--timeout", "600"]