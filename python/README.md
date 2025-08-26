# Build Instructions

## Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (requires PostgreSQL)
export DATABASE_URL="postgresql://user:password@localhost/transactions"
python app.py
```

## Docker Build
```bash
# Build the Docker image
docker build -t transaction-api-python:latest .

# Run with Docker (requires PostgreSQL container or external DB)
docker run -p 8080:8080 \
  -e DATABASE_URL="postgresql://user:password@db:5432/transactions" \
  transaction-api-python:latest
```

## API Usage Examples
```bash
# Create a transaction
curl -X POST http://localhost:8080/transactions \
  -H "Content-Type: application/json" \
  -d '{"value": 150.75, "timestamp": "2025-08-20T14:30:00"}'

# List transactions
curl http://localhost:8080/transactions

# Get specific transaction
curl http://localhost:8080/transactions/1

# Health check
curl http://localhost:8080/health

# Metrics (Prometheus format)
curl http://localhost:8080/metrics
```