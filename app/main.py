import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database.connection import engine, Base
from app.api.events import router as events_router
from app.api.mock_webhook import router as mock_webhook_router
from app.worker.background_worker import BackgroundWorker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("app.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan events of the FastAPI application.
    Creates SQLite tables on startup and starts the custom background worker.
    Terminates the worker thread cleanly on shutdown.
    """
    logger.info("Initializing database and tables...")
    Base.metadata.create_all(bind=engine)
    
    # Startup recovery for processing events
    from app.database.connection import SessionLocal
    from app.models.event import Event
    logger.info("Running recovery logic for stuck 'processing' events...")
    db = SessionLocal()
    try:
        recovered = db.query(Event).filter(Event.status == "processing").update(
            {"status": "pending"}, synchronize_session=False
        )
        db.commit()
        if recovered > 0:
            logger.info(f"Recovered {recovered} stuck 'processing' events to 'pending'.")
    except Exception as e:
        db.rollback()
        logger.error(f"Startup recovery failed: {str(e)}")
    finally:
        db.close()
        
    logger.info("Starting background webhook delivery worker...")
    worker = BackgroundWorker()
    worker.start()
    app.state.worker = worker
    
    yield
    
    logger.info("Stopping background webhook delivery worker...")
    worker.stop()
    worker.join(timeout=5.0)
    logger.info("Application shutdown complete.")

app = FastAPI(
    title="Nestack Webhook Delivery Engine",
    description="A robust, custom background webhook delivery engine built as part of the Nestack SDE Assessment.",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(events_router)
app.include_router(mock_webhook_router)

@app.get("/")
def get_root_status():
    """Root endpoint to check engine health and status."""
    return {
        "status": "healthy",
        "engine": "Nestack Webhook Delivery Engine",
        "background_worker": "running"
    }
