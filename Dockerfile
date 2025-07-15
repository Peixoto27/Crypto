FROM python:3.9-slim
WORKDIR /app
COPY requirements/base.txt ./requirements/
RUN pip install --no-cache-dir -r requirements/base.txt
COPY src/ .
EXPOSE 8000
CMD ["gunicorn", "app.main:app", "--workers", "4", "--bind", "0.0.0.0:8000"]


