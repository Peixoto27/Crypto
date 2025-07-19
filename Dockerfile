FROM python:3.9-slim

WORKDIR /app

# Instala dependências básicas
RUN apt-get update && apt-get install -y \
    gcc python3-dev libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copia e instala requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .

EXPOSE 8000
CMD ["gunicorn", "app.main:app", "--workers", "4", "--bind", "0.0.0.0:8000"]
