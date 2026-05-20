import os
import time
import json
import logging
from datetime import datetime
from typing import List, Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, validator
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

# OpenTelemetry imports
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor


# ---------------------------------------------------------
# Service Configuration
# ---------------------------------------------------------

SERVICE_NAME = os.getenv("SERVICE_NAME", "transaction-api")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "1.0.0")
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://otel-collector:4318/v1/traces"
)


# ---------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "environment": ENVIRONMENT,
            "message": record.getMessage(),
            "logger": record.name,
        }

        span = trace.get_current_span()
        span_context = span.get_span_context()

        if span_context and span_context.is_valid:
            log_record["trace_id"] = format(span_context.trace_id, "032x")
            log_record["span_id"] = format(span_context.span_id, "016x")

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())

logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(SERVICE_NAME)

LoggingInstrumentor().instrument(set_logging_format=False)


# ---------------------------------------------------------
# OpenTelemetry Tracing Setup
# ---------------------------------------------------------

resource = Resource.create({
    "service.name": SERVICE_NAME,
    "service.version": SERVICE_VERSION,
    "deployment.environment": ENVIRONMENT,
})

trace_provider = TracerProvider(resource=resource)

otlp_exporter = OTLPSpanExporter(endpoint=OTEL_EXPORTER_OTLP_ENDPOINT)
trace_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer(SERVICE_NAME)

# Auto-instrument asyncpg DB calls
AsyncPGInstrumentor().instrument()


# ---------------------------------------------------------
# Prometheus Metrics
# ---------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"]
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "status_code"]
)

transactions_total = Counter(
    "transactions_total",
    "Total transactions processed",
    ["status"]
)

transaction_value_total = Counter(
    "transaction_value_total",
    "Total value of completed transactions"
)

db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"]
)

db_errors_total = Counter(
    "db_errors_total",
    "Total database errors",
    ["operation"]
)

app_health_status = Gauge(
    "app_health_status",
    "Application health status. 1 means healthy, 0 means unhealthy"
)


# ---------------------------------------------------------
# Models
# ---------------------------------------------------------

class Transaction(BaseModel):
    id: Optional[int] = None
    value: float
    timestamp: datetime
    status: str = "completed"
    created_at: Optional[datetime] = None

    @validator("value")
    def value_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Transaction value must be positive")
        return v


class TransactionRequest(BaseModel):
    value: float
    timestamp: datetime

    @validator("value")
    def value_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Transaction value must be positive")
        return v


# ---------------------------------------------------------
# Middleware for Metrics + Trace Context
# ---------------------------------------------------------

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        endpoint = request.url.path
        method = request.method

        with tracer.start_as_current_span("http_request_metrics") as span:
            span.set_attribute("http.method", method)
            span.set_attribute("http.route", endpoint)

            try:
                response = await call_next(request)
                status_code = str(response.status_code)

                span.set_attribute("http.status_code", response.status_code)

                if response.status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))

                return response

            except Exception as e:
                status_code = "500"
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                logger.exception("Unhandled request error")
                raise

            finally:
                duration = time.time() - start_time

                http_requests_total.labels(
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code
                ).inc()

                http_request_duration_seconds.labels(
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code
                ).observe(duration)


# ---------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------

app = FastAPI(
    title="Transaction API",
    version=SERVICE_VERSION,
    description="Transaction API with Prometheus and OpenTelemetry observability"
)

app.add_middleware(MetricsMiddleware)

# Auto-instrument FastAPI routes
FastAPIInstrumentor.instrument_app(app, tracer_provider=trace_provider)

db_pool = None


# ---------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------

@app.on_event("startup")
async def startup():
    global db_pool

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://user:password@localhost/transactions"
    )

    with tracer.start_as_current_span("app_startup") as span:
        try:
            logger.info("Starting database connection pool")

            db_pool = await asyncpg.create_pool(
                database_url,
                min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
                max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
                command_timeout=30
            )

            async with db_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        value DECIMAL(15,2) NOT NULL,
                        timestamp TIMESTAMP NOT NULL,
                        status VARCHAR(50) DEFAULT 'completed',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)

            app_health_status.set(1)
            logger.info("Database connected and table verified")

        except Exception as e:
            app_health_status.set(0)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            logger.exception("Failed to connect to database")
            raise


@app.on_event("shutdown")
async def shutdown():
    global db_pool

    with tracer.start_as_current_span("app_shutdown"):
        if db_pool:
            await db_pool.close()
            logger.info("Database connection pool closed")


# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------

@app.post("/transactions", response_model=Transaction, status_code=201)
async def create_transaction(transaction: TransactionRequest):
    start_time = time.time()

    with tracer.start_as_current_span("create_transaction") as span:
        span.set_attribute("transaction.value", transaction.value)
        span.set_attribute("transaction.timestamp", transaction.timestamp.isoformat())

        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO transactions (value, timestamp, status, created_at)
                    VALUES ($1, $2, 'completed', CURRENT_TIMESTAMP)
                    RETURNING id, value, timestamp, status, created_at
                    """,
                    transaction.value,
                    transaction.timestamp.replace(tzinfo=None)
                    if transaction.timestamp.tzinfo
                    else transaction.timestamp
                )

            duration = time.time() - start_time
            db_query_duration_seconds.labels(operation="insert_transaction").observe(duration)

            transactions_total.labels(status="completed").inc()
            transaction_value_total.inc(transaction.value)

            span.set_attribute("transaction.id", row["id"])
            span.set_attribute("transaction.status", row["status"])

            logger.info(
                f"Transaction created successfully. transaction_id={row['id']}, value={transaction.value}"
            )

            return Transaction(
                id=row["id"],
                value=float(row["value"]),
                timestamp=row["timestamp"],
                status=row["status"],
                created_at=row["created_at"]
            )

        except Exception as e:
            db_errors_total.labels(operation="insert_transaction").inc()
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))

            logger.exception("Database error creating transaction")

            raise HTTPException(
                status_code=500,
                detail="Internal server error"
            )


@app.get("/transactions", response_model=List[Transaction])
async def list_transactions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    start_time = time.time()

    with tracer.start_as_current_span("list_transactions") as span:
        span.set_attribute("query.limit", limit)
        span.set_attribute("query.offset", offset)

        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, value, timestamp, status, created_at
                    FROM transactions
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                    """,
                    limit,
                    offset
                )

            duration = time.time() - start_time
            db_query_duration_seconds.labels(operation="list_transactions").observe(duration)

            span.set_attribute("transaction.count", len(rows))

            logger.info(f"Fetched transactions count={len(rows)}")

            return [
                Transaction(
                    id=row["id"],
                    value=float(row["value"]),
                    timestamp=row["timestamp"],
                    status=row["status"],
                    created_at=row["created_at"]
                )
                for row in rows
            ]

        except Exception as e:
            db_errors_total.labels(operation="list_transactions").inc()
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))

            logger.exception("Database error listing transactions")

            raise HTTPException(
                status_code=500,
                detail="Internal server error"
            )


@app.get("/transactions/{transaction_id}", response_model=Transaction)
async def get_transaction(transaction_id: int):
    start_time = time.time()

    with tracer.start_as_current_span("get_transaction") as span:
        span.set_attribute("transaction.id", transaction_id)

        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, value, timestamp, status, created_at
                    FROM transactions
                    WHERE id = $1
                    """,
                    transaction_id
                )

            duration = time.time() - start_time
            db_query_duration_seconds.labels(operation="get_transaction").observe(duration)

            if not row:
                span.set_attribute("transaction.found", False)
                logger.warning(f"Transaction not found. transaction_id={transaction_id}")
                raise HTTPException(status_code=404, detail="Transaction not found")

            span.set_attribute("transaction.found", True)

            logger.info(f"Transaction fetched successfully. transaction_id={transaction_id}")

            return Transaction(
                id=row["id"],
                value=float(row["value"]),
                timestamp=row["timestamp"],
                status=row["status"],
                created_at=row["created_at"]
            )

        except HTTPException:
            raise

        except Exception as e:
            db_errors_total.labels(operation="get_transaction").inc()
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))

            logger.exception("Database error getting transaction")

            raise HTTPException(
                status_code=500,
                detail="Internal server error"
            )


# ---------------------------------------------------------
# Health Endpoints
# ---------------------------------------------------------

@app.get("/health")
async def health_check():
    with tracer.start_as_current_span("health_check") as span:
        try:
            async with db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

            app_health_status.set(1)
            span.set_attribute("health.database", "connected")

            return {
                "status": "healthy",
                "time": datetime.utcnow().isoformat(),
                "database": "connected",
                "service": SERVICE_NAME,
                "environment": ENVIRONMENT
            }

        except Exception as e:
            app_health_status.set(0)
            db_errors_total.labels(operation="health_check").inc()

            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.set_attribute("health.database", "disconnected")

            logger.exception("Health check failed")

            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unhealthy",
                    "error": "database connection failed"
                }
            )


@app.get("/ready")
async def readiness_check():
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialized")

    return {
        "status": "ready",
        "service": SERVICE_NAME,
        "time": datetime.utcnow().isoformat()
    }


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        access_log=False
    )
