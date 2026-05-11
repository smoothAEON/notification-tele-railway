# OANDA Alert MCP Service

Railway-hosted MCP Streamable HTTP service for one-shot OANDA price alerts.

The service watches only instruments with active alerts, fires alerts on mid-price crossings, sends notifications to the configured Telegram chat, and exposes alert operations to another MCP server over HTTP. Explicit price reads can still request any valid OANDA instrument on demand.

## MCP Endpoint

- Health: `GET /health`
- MCP Streamable HTTP: `POST/GET/DELETE /mcp` by default
- Auth: `Authorization: Bearer <MCP_HTTP_API_KEY>`

The MCP endpoint path can be changed with `MCP_HTTP_PATH`.

## Tools

- `get_service_status()`
- `get_current_prices(instruments: list[str] | None = None)`
- `create_price_alert(instrument, target_price, direction, note = None)`
- `list_current_alerts(instrument = None)`
- `list_fired_alerts(instrument = None, limit = 50)`
- `cancel_alert(alert_id)`
- `cancel_current_alerts(instrument = None)`

Alert `direction` must be `above` or `below`. Trigger checks use mid price: `(bid + ask) / 2`.

## Environment

Required:

```env
OANDA_API_KEY=
OANDA_ACCOUNT_ID=
OANDA_ENVIRONMENT=practice
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
MCP_HTTP_API_KEY=
```

Optional:

```env
LOG_LEVEL=INFO
LOG_JSON=false
# Deprecated/diagnostic only. Background monitoring is driven by active alerts.
STREAM_INSTRUMENTS=
MCP_HTTP_HOST=0.0.0.0
MCP_HTTP_PORT=8000
MCP_HTTP_PATH=/mcp
ALERT_DB_PATH=/data/alerts.db
```

For local development, set `ALERT_DB_PATH=data/alerts.db`. On Railway, mount a volume and keep the default `/data/alerts.db`.

`STREAM_INSTRUMENTS` is reported in status for diagnostics only. It does not create an always-on OANDA stream subscription.

## Local Run

```powershell
python -m pip install -r requirements.txt
python -m alert_mcp.main
```

Railway should use the included `Procfile`. The service binds to `0.0.0.0:$PORT` when Railway injects `PORT`, otherwise it uses `MCP_HTTP_PORT`.

## Tests

```powershell
python -m pip install -e .[test]
python -m pytest -q
```

The default tests use fakes and do not hit live OANDA or Telegram.

## Deployment Notes

- Use one Railway replica with SQLite volume storage.
- Keep `.env` out of git.
- Set Railway healthcheck path to `/health`.
- Share the public Railway URL plus bearer token only with the MCP server that should manage alerts.
