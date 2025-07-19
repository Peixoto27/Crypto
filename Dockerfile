FROM python:3.9-slim

WORKDIR /app

# Instala TA-Lib e dependências
RUN apt-get update && \
    apt-get install -y build-essential && \
    wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib/ && \
    ./configure --prefix=/usr && \
    make && \
    make install

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "app:app", "-b", "0.0.0.0:${PORT:-8000}", "--access-logfile", "-"]
