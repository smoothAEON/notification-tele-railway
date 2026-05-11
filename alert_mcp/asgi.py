"""ASGI app factory with MCP auth and Railway health endpoint."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from alert_mcp.mcp_server import build_mcp_server
from alert_mcp.service import AlertService, build_service
from alert_mcp.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    """Log HTTP request entry, response headers, completion, and exceptions."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        method = scope.get("method", "")
        path = scope.get("path", "")
        query_present = bool(scope.get("query_string", b""))
        headers = _selected_headers(scope)
        client = _client_host(scope)
        status_code: int | None = None

        logger.info(
            "http request started method=%s path=%s query_present=%s client=%s headers=%s",
            method,
            path,
            query_present,
            client,
            headers,
        )

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                logger.info(
                    "http response started method=%s path=%s status=%s elapsed_ms=%.1f",
                    method,
                    path,
                    status_code,
                    _elapsed_ms(started_at),
                )
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                logger.info(
                    "http request completed method=%s path=%s status=%s elapsed_ms=%.1f",
                    method,
                    path,
                    status_code,
                    _elapsed_ms(started_at),
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception(
                "http request failed method=%s path=%s status=%s elapsed_ms=%.1f",
                method,
                path,
                status_code,
                _elapsed_ms(started_at),
            )
            raise


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


def _client_host(scope) -> str:
    client = scope.get("client")
    if not client:
        return "-"
    return str(client[0])


def _selected_headers(scope) -> dict[str, str]:
    raw_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    return {
        key: raw_headers[key]
        for key in (
            "host",
            "user-agent",
            "accept",
            "content-type",
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-request-id",
            "cf-connecting-ip",
        )
        if key in raw_headers
    }


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require a bearer token for the MCP endpoint."""

    def __init__(self, app, *, token: str, protected_path: str) -> None:
        super().__init__(app)
        self._token = token
        self._protected_path = protected_path.rstrip("/") or "/"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._is_protected(request.url.path):
            expected = f"Bearer {self._token}"
            if request.headers.get("authorization", "") != expected:
                logger.warning(
                    "mcp auth rejected method=%s path=%s client=%s auth_present=%s",
                    request.method,
                    request.url.path,
                    request.client.host if request.client else "-",
                    bool(request.headers.get("authorization")),
                )
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            logger.info(
                "mcp auth accepted method=%s path=%s client=%s",
                request.method,
                request.url.path,
                request.client.host if request.client else "-",
            )
        return await call_next(request)

    def _is_protected(self, path: str) -> bool:
        if self._protected_path == "/":
            return path != "/health"
        return path == self._protected_path or path.startswith(f"{self._protected_path}/")


async def health(request: Request) -> JSONResponse:
    service = request.app.state.alert_service
    monitor_running = bool(service._monitor_task and not service._monitor_task.done())
    logger.info("health check ok monitor_running=%s", monitor_running)
    return JSONResponse(
        {
            "ok": True,
            "service": "oanda-alert-mcp",
            "monitor_running": monitor_running,
        }
    )


def create_app(*, settings: Settings | None = None, service: AlertService | None = None) -> Starlette:
    resolved_settings = settings or get_settings()
    resolved_service = service or build_service(resolved_settings)
    mcp = build_mcp_server(service=resolved_service, settings=resolved_settings)

    logger.info(
        "creating ASGI app mcp_path=%s db_path=%s oanda_environment=%s",
        resolved_settings.mcp_http_path,
        resolved_settings.alert_db_path,
        resolved_settings.oanda_environment,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        logger.info("ASGI lifespan startup begin")
        app.state.alert_service = resolved_service
        await resolved_service.start()
        logger.info("alert service startup complete")
        async with mcp.session_manager.run():
            logger.info("MCP session manager running")
            yield
        logger.info("ASGI lifespan shutdown begin")
        await resolved_service.stop()
        logger.info("ASGI lifespan shutdown complete")

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        middleware=[
            Middleware(RequestLoggingMiddleware),
            Middleware(
                BearerAuthMiddleware,
                token=resolved_settings.mcp_http_api_key.get_secret_value(),
                protected_path=resolved_settings.mcp_http_path,
            )
        ],
        lifespan=lifespan,
    )
    app.state.alert_service = resolved_service
    return app


_lazy_app: Starlette | None = None


async def app(scope, receive, send) -> None:
    """Lazy ASGI callable for `uvicorn alert_mcp.asgi:app` deployments."""

    global _lazy_app
    if _lazy_app is None:
        _lazy_app = create_app()
    await _lazy_app(scope, receive, send)
