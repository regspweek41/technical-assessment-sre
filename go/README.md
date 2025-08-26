# Build Instructions

## Local Development
```bash
# Install dependencies
go mod tidy

# Run locally (requires PostgreSQL)
export DATABASE_URL="postgres://user:password@localhost/transactions?sslmode=disable"
go run main.go
```

## Docker Build
```bash
# Build the Docker image
docker build -t transaction-api-go:latest .

# Run with Docker (requires PostgreSQL container or external DB)
docker run -p 8080:8080 \
  -e DATABASE_URL="postgres://user:password@db:5432/transactions?sslmode=disable" \
  transaction-api-go:latest
```

## API Usage Examples
```bash
# Create a transaction
curl -X POST http://localhost:8080/transactions \
  -H "Content-Type: application/json" \
  -d '{"value": 150.75, "timestamp": "2025-08-20T14:30:00Z"}'

# List transactions
curl http://localhost:8080/transactions

# Get specific transaction
curl http://localhost:8080/transactions/1

# Health check
curl http://localhost:8080/health

# Metrics (Prometheus format)
curl http://localhost:8080/metrics
```
