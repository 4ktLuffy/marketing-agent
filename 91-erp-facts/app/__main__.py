import os
import sys

import uvicorn

from . import config
from .main import create_app

if __name__ == "__main__":
    try:
        app = create_app()
    except Exception as e:  # ConfigError, MappingError: names and reasons only, no values
        print(f"erp-facts: {e}", file=sys.stderr)
        sys.exit(1)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
