import logging
from backend.persistence import init_db
from backend.polling import start_polling

logger = logging.getLogger(__name__)


async def startup_event():
    """Initialize database and start polling on app startup."""
    try:
        logger.info("Initializing database...")
        init_db()
        logger.info("Database initialized")

        logger.info("Starting polling orchestrator...")
        start_polling()
        logger.info("Polling orchestrator started")

    except Exception as e:
        logger.error(f"Error during startup: {e}", exc_info=True)
        raise


async def shutdown_event():
    """Stop polling on app shutdown."""
    try:
        logger.info("Stopping polling orchestrator...")
        from backend.polling import stop_polling
        stop_polling()
        logger.info("Polling orchestrator stopped")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)
