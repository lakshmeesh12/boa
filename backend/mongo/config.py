# engine/db/mongo/config.py
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
import logging

load_dotenv()

logger = logging.getLogger(__name__)

# Get MongoDB configuration from environment
MONGODB_URI = os.environ.get("MONGODB_URI")
DATABASE_NAME = os.environ.get("DATABASE_NAME")
USER_NAME = os.environ.get("USER_NAME", "").strip()
PASSWORD = os.environ.get("PASSWORD", "").strip()

# Validate required environment variables
if not MONGODB_URI:
    raise ValueError("MONGODB_URI must be set in environment variables")

if not DATABASE_NAME:
    raise ValueError("DATABASE_NAME must be set in environment variables")

# Log environment (without sensitive data)
is_docker = os.getenv('DOCKER', 'false').lower() == 'true'
env_mode = "Docker" if is_docker else "Local"
logger.info(f"Running in {env_mode} mode")
logger.info(f"MongoDB Database: {DATABASE_NAME}")

# Initialize MongoDB client
# If MONGODB_URI already contains credentials (mongodb://user:pass@host:port),
# use it directly. Only construct URI if separate credentials are provided
# AND the URI doesn't already contain them.

if USER_NAME and PASSWORD and "@" not in MONGODB_URI:
    # Separate credentials provided and URI doesn't contain them
    logger.info("Building MongoDB URI with separate credentials")
    USER_NAME = quote_plus(USER_NAME)
    PASSWORD = quote_plus(PASSWORD)
    
    if MONGODB_URI.startswith("mongodb+srv://") or "mongodb.net" in MONGODB_URI:
        # MongoDB Atlas
        host = MONGODB_URI.split('://')[1]
        mongo_client = AsyncIOMotorClient(
            f"mongodb+srv://{USER_NAME}:{PASSWORD}@{host}"
        )
        logger.info("Connected to MongoDB Atlas")
    else:
        # Local or custom host - extract host:port
        host = MONGODB_URI.replace("mongodb://", "")
        mongo_client = AsyncIOMotorClient(
            f"mongodb://{USER_NAME}:{PASSWORD}@{host}"
        )
        logger.info(f"Connected to MongoDB at {host}")
else:
    # Use MONGODB_URI as-is (either it has credentials already, or no auth needed)
    logger.info("Using MongoDB URI as-is (credentials included or no auth)")
    mongo_client = AsyncIOMotorClient(MONGODB_URI)

db = mongo_client[DATABASE_NAME]


async def ensure_ingestion_indexes() -> None:
    """
    Create indexes that accelerate ingestion reads/writes.
    Safe to call on every startup — Motor skips existing indexes.
    """
    try:
        # en_semantic_chunks: filtered by tenant/workspace/session, and by document_id
        chunks = db["en_semantic_chunks"]
        await chunks.create_index(
            [("tenant_id", 1), ("workspace_id", 1), ("session_id", 1)],
            background=True, name="chunks_scope_idx",
        )
        await chunks.create_index(
            [("document_id", 1)],
            background=True, name="chunks_doc_idx",
        )

        # en_excel_documents: filtered by scope, filename lookups, status checks
        docs = db["en_excel_documents"]
        await docs.create_index(
            [("tenant_id", 1), ("workspace_id", 1), ("session_id", 1)],
            background=True, name="docs_scope_idx",
        )
        await docs.create_index(
            [("filename", 1)],
            background=True, name="docs_filename_idx",
        )
        await docs.create_index(
            [("upload_scope", 1)],
            background=True, name="docs_scope_type_idx",
        )

        logger.info("✓ MongoDB ingestion indexes ensured")
    except Exception as e:
        logger.warning("MongoDB index creation warning (non-fatal): %s", e)


@asynccontextmanager
async def mongo_session(collection_name):
    """
    Async context manager for MongoDB collection access.
    """
    try:
        yield db[collection_name]
    finally:
        pass  # No explicit cleanup needed for Motor client