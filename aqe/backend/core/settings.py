"""AQE runtime settings — loaded from the root .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Walk up to find the root .env (boa/.env sits two levels above aqe/backend/)
_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    # Claude
    claude_api_key: str
    claude_model_opus: str
    claude_model_sonnet: str

    # OpenAI (embeddings)
    openai_api_key: str
    openai_embedding_model: str
    embedding_dim: int

    # Qdrant
    qdrant_url: str
    qdrant_api_key: str
    qdrant_collection: str

    # Neo4j
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str

    # Target machine
    target_api_url: str
    target_ui_url: str
    target_ui_username: str
    target_ui_password: str
    # API auth: none | bearer | api_key | basic
    target_api_auth_type: str
    target_api_token: str

    # AQE server
    aqe_api_port: int
    aqe_host: str
    log_level: str

    # Storage
    data_dir: Path
    reports_dir: Path
    scripts_dir: Path

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(os.getenv("AQE_DATA_DIR", str(_ROOT / "aqe" / "data")))
        return cls(
            claude_api_key=os.environ["CLAUDE_API_KEY"],
            claude_model_opus=os.getenv("CLAUDE_MODEL_OPUS", "claude-opus-4-7"),
            claude_model_sonnet=os.getenv("CLAUDE_MODEL_SONNET", "claude-sonnet-4-6"),

            openai_api_key=os.environ["OPENAI_API_KEY"],
            openai_embedding_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large"),
            embedding_dim=int(os.getenv("EMBEDDING_DIM", "3072")),

            qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            qdrant_api_key=os.getenv("QDRANT_API_KEY", ""),
            qdrant_collection=os.getenv("QDRANT_COLLECTION_LOGS", "boa_logs"),

            neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=os.getenv("NEO4J_PASSWORD", "test12345"),

            target_api_url=os.getenv("TARGET_API_URL", "http://localhost:8000"),
            target_ui_url=os.getenv("TARGET_UI_URL", "http://localhost:8080"),
            target_ui_username=os.getenv("TARGET_UI_USERNAME", ""),
            target_ui_password=os.getenv("TARGET_UI_PASSWORD", ""),
            target_api_auth_type=os.getenv("TARGET_API_AUTH_TYPE", "none"),
            target_api_token=os.getenv("TARGET_API_TOKEN", ""),

            aqe_api_port=int(os.getenv("AQE_API_PORT", "5001")),
            aqe_host=os.getenv("AQE_HOST", "0.0.0.0"),
            log_level=os.getenv("AQE_LOG_LEVEL", "INFO"),

            data_dir=data_dir,
            reports_dir=data_dir / "reports",
            scripts_dir=data_dir / "scripts",
        )


settings = Settings.load()

# Ensure data directories exist
for _d in (settings.data_dir, settings.reports_dir, settings.scripts_dir):
    _d.mkdir(parents=True, exist_ok=True)
