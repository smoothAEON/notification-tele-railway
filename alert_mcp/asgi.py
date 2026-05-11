"""ASGI app factory with MCP auth and Railway health endpoint."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from alert_mcp.mcp_server import build_mcp_server
from alert_mcp.service import AlertService, build_service
from alert_mcp.settings import Settings, get_settings


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
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    def _is_protected(self, path: str) -> bool:
        if self._protected_path == "/":
            return path != "/health"
        return path == self._protected_path or path.startswith(f"{self._protected_path}/")


async def health(request: Request) -> JSONResponse:
    service = request.app.state.alert_service
    return JSONResponse(
        {
            "ok": True,
            "service": "oanda-alert-mcp",
            "monitor_running": bool(service._monitor_task and not service._monitor_task.done()),
        }
    )


def create_app(*, settings: Settings | None = None, service: AlertService | None = None) -> Starlette:
    resolved_settings = settings or get_settings()
    resolved_service = service or build_service(resolved_settings)
    mcp = build_mcp_server(service=resolved_service, settings=resolved_settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.alert_service = resolved_service
        await resolved_service.start()
        async with mcp.session_manager.run():
            yield
        await resolved_service.stop()

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        middleware=[
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
