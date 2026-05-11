"""CLI entrypoint."""

from __future__ import annotations

import logging

import uvicorn

from alert_mcp.asgi import create_app
from alert_mcp.logging_config import configure_logging
from alert_mcp.settings import load_settings


def main() -> int:
    settings = load_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    logger = logging.getLogger(__name__)
    logger.info(
        "starting OANDA Alert MCP on %s:%s path=%s",
        settings.mcp_http_host,
        settings.effective_port,
        settings.mcp_http_path,
    )
    uvicorn.run(
        create_app(settings=settings),
        host=settings.mcp_http_host,
        port=settings.effective_port,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

