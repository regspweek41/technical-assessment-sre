# Transaction API Services

This repository contains two implementations of a transaction API service:

- **Go Service** - Located in `./go/` - See [Go README](./go/README.md)
- **Python Service** - Located in `./python/` - See [Python README](./python/README.md)

Both services provide identical REST APIs for managing financial transactions with PostgreSQL database integration and Prometheus metrics.

## Database Setup

The services require a PostgreSQL database with the following configuration:

### Local Development with Docker

```bash
# Start PostgreSQL database
docker run -d \
  --name transaction-db \
  -e POSTGRES_USER=root \
  -e POSTGRES_PASSWORD=rootpassword \
  -e POSTGRES_DB=transactions \
  -p 5432:5432 \
  postgres:15
```

### Kubernetes Deployment

Deploy the database using the provided Kubernetes templates:

```bash
# Deploy PostgreSQL
kubectl apply -f database/postgresql.yaml
```

The database will be initialized by the Go/Python services on first connection with the required `transactions` table schema.

### Database Schema

The `transactions` table includes:
- `id` (Serial Primary Key)
- `user_id` (Integer)
- `amount` (Decimal 10,2)
- `type` (VARCHAR 20) - 'credit' or 'debit'  
- `description` (Text)
- `created_at` (Timestamp)

## API Endpoints

Both services expose identical REST APIs:

- `POST /transactions` - Create a new transaction
- `GET /transactions` - List transactions (with pagination)
- `GET /transactions/{id}` - Get specific transaction
- `GET /health` - Health check endpoint
- `GET /metrics` - Prometheus metrics

## Environment Variables

Both services use the `DATABASE_URL` environment variable:

```
DATABASE_URL=postgresql://root:rootpassword@localhost:5432/transactions?sslmode=disable
```

## Quick Start

1. Start the database:
   ```bash
   docker run -d --name transaction-db -e POSTGRES_USER=root -e POSTGRES_PASSWORD=rootpassword -e POSTGRES_DB=transactions -p 5432:5432 postgres:15
   ```

2. Choose your service implementation:
   - [Go Service Setup](./go/README.md)
   - [Python Service Setup](./python/README.md)

## Kubernetes Deployment

For full Kubernetes deployment with both services:

```bash
# Deploy database
kubectl apply -f database/postgresql.yaml

# Deploy Go service  
kubectl apply -f go/k8s/

# Deploy Python service
kubectl apply -f python/k8s/
```

## Architecture

```
┌─────────────────┐    ┌─────────────────┐
│   Go Service    │    │ Python Service  │
│   (Port 8080)   │    │   (Port 8080)   │
└─────────┬───────┘    └─────────┬───────┘
          │                      │
          └──────────┬───────────┘
                     │
         ┌───────────▼────────────┐
         │   PostgreSQL Database  │
         │      (Port 5432)       │
         └────────────────────────┘
```

Both services are independently deployable and can run simultaneously, sharing the same PostgreSQL database.