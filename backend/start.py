import sys
from pathlib import Path

backend_dir = Path(__file__).parent.resolve()
sys.path.insert(0, str(backend_dir))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        app_dir=str(backend_dir),
        log_level="debug",
    )