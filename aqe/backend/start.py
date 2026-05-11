"""AQE entry point — starts the FastAPI server."""
import sys
from pathlib import Path

# Ensure backend/ is on sys.path (for local imports)
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn
from core.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.aqe_host,
        port=settings.aqe_api_port,
        reload=False,
        log_level=settings.log_level.lower(),
    )
