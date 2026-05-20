FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=transaction-api
ENV SERVICE_VERSION=1.0.0
ENV ENVIRONMENT=dev
ENV PORT=8080

RUN addgroup --system appgroup && adduser --system appuser --ingroup appgroup

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

USER appuser

EXPOSE 8080

CMD ["python", "main.py"]
