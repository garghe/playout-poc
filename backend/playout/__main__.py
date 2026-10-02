import logging

import uvicorn

from .config import CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
uvicorn.run("playout.api:app", host="0.0.0.0", port=CONFIG.http_port, log_level="warning", loop="asyncio")
