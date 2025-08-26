import os
import time
import logging
from datetime import datetime
from typing import List, Optional
import asyncio

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, validator
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prometheus metrics
http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code']
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint', 'status_code']
)

transactions_total = Counter(
    'transactions_total',
    'Total transactions processed',
    ['status']
)

class Transaction(BaseModel):
    id: Optional[int] = None
    value: float
    timestamp: datetime
    status: str = "completed"
    created_at: Optional[datetime] = None

    @validator('value')
    def value_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Transaction value must be positive')
        return v

class TransactionRequest(BaseModel):
    value: float
    timestamp: datetime

    @validator('value')
    def value_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Transaction value must be positive')
        return v

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start_time = time.time()
        
        response = await call_next(request)
        
        duration = time.time() - start_time
        endpoint = request.url.path
        method = request.method
        status_code = str(response.status_code)
        
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
        
        return response

app = FastAPI(title="Transaction API", version="1.0.0")
app.add_middleware(MetricsMiddleware)

# Database connection pool
db_pool = None

@app.on_event("startup")
async def startup():
    global db_pool
    database_url = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/transactions")
    
    try:
        db_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
        
        # Create table if not exists
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
        logger.info("Database connected and table created")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise

@app.on_event("shutdown")
async def shutdown():
    global db_pool
    if db_pool:
        await db_pool.close()

@app.post("/transactions", response_model=Transaction, status_code=201)
async def create_transaction(transaction: TransactionRequest):
    async with db_pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO transactions (value, timestamp, status, created_at)
                VALUES ($1, $2, 'completed', CURRENT_TIMESTAMP)
                RETURNING id, value, timestamp, status, created_at
                """,
                transaction.value,
                transaction.timestamp.replace(tzinfo=None) if transaction.timestamp.tzinfo else transaction.timestamp
            )
            
            transactions_total.labels(status="completed").inc()
            
            return Transaction(
                id=row['id'],
                value=row['value'],
                timestamp=row['timestamp'],
                status=row['status'],
                created_at=row['created_at']
            )
        except Exception as e:
            logger.error(f"Database error creating transaction: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/transactions", response_model=List[Transaction])
async def list_transactions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    async with db_pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT id, value, timestamp, status, created_at 
                FROM transactions 
                ORDER BY created_at DESC 
                LIMIT $1 OFFSET $2
                """,
                limit, offset
            )
            
            return [
                Transaction(
                    id=row['id'],
                    value=row['value'],
                    timestamp=row['timestamp'],
                    status=row['status'],
                    created_at=row['created_at']
                )
                for row in rows
            ]
        except Exception as e:
            logger.error(f"Database error listing transactions: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/transactions/{transaction_id}", response_model=Transaction)
async def get_transaction(transaction_id: int):
    async with db_pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                SELECT id, value, timestamp, status, created_at 
                FROM transactions 
                WHERE id = $1
                """,
                transaction_id
            )
            
            if not row:
                raise HTTPException(status_code=404, detail="Transaction not found")
            
            return Transaction(
                id=row['id'],
                value=row['value'],
                timestamp=row['timestamp'],
                status=row['status'],
                created_at=row['created_at']
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Database error getting transaction: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health_check():
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        
        return {
            "status": "healthy",
            "time": datetime.utcnow().isoformat(),
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=503,
            detail={"status": "unhealthy", "error": "database connection failed"}
        )

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)