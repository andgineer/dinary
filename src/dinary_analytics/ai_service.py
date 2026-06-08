"""dinary-ai service: MCP ledger tools plus health and refresh-now HTTP routes."""

import argparse
import datetime
import json

import duckdb
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from dinary_analytics.connection import LEDGER_SCHEMA, open_ledger
from dinary_analytics.paths import MCP_PORT
from dinary_analytics.refresh import (
    get_db_path,
    get_last_refresh,
    get_last_refresh_error,
    start_refresh_daemon,
    trigger_refresh_now,
)
from dinary_analytics.settings import (
    delete_view,
    get_config,
    get_view,
    list_view_ids,
    save_view,
    set_config,
)

mcp = FastMCP("dinary-analytics")


def _run_query(sql: str) -> str:
    """Execute sql against the ledger replica and return JSON rows."""
    db_path = get_db_path()
    if db_path is None:
        raise ToolError(
            "ledger replica is not yet available — dinary-ai hasn't completed its first refresh",
        )
    con = open_ledger(db_path)
    try:
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return json.dumps([dict(zip(columns, row, strict=True)) for row in rows], default=str)
    except duckdb.Error as exc:
        raise ToolError(f"query failed: {exc}") from exc
    finally:
        con.close()


@mcp.tool()
def query(sql: str) -> str:
    """Execute a read-only SQL query against the dinary expense ledger.

    Tables live in the 'ledger' schema — e.g. ledger.expenses, ledger.categories.
    Only SELECT statements are accepted.
    """
    return _run_query(sql)


@mcp.tool()
def schema() -> str:
    """Return the ledger database schema as SQL CREATE statements for LLM context."""
    return LEDGER_SCHEMA


@mcp.tool()
def get_config_tool(key: str) -> str:
    """Read a config entry from analytics.db."""
    val = get_config(key)
    return val if val is not None else ""


@mcp.tool()
def set_config_tool(key: str, value: str) -> str:
    """Write a config entry to analytics.db."""
    set_config(key, value)
    return "ok"


@mcp.tool()
def list_views() -> str:
    """List all saved analytics view IDs and names as JSON."""
    ids = list_view_ids()
    result = []
    for view_id in ids:
        cfg = get_view(view_id)
        result.append({"id": view_id, "name": cfg.get("name", "") if cfg else ""})
    return json.dumps(result)


@mcp.tool()
def get_view_tool(view_id: str) -> str:
    """Get a saved analytics view config as JSON."""
    cfg = get_view(view_id)
    return json.dumps(cfg) if cfg is not None else ""


@mcp.tool()
def save_view_tool(config: str) -> str:
    """Save an analytics view config (JSON string). Returns the assigned ID."""
    return save_view(json.loads(config))


@mcp.tool()
def delete_view_tool(view_id: str) -> str:
    """Delete a saved analytics view by ID."""
    delete_view(view_id)
    return "ok"


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Report whether the ledger replica is queryable and when it was last refreshed."""
    last_refresh = get_last_refresh()
    return JSONResponse(
        {
            "ok": get_db_path() is not None,
            "last_refresh": (
                datetime.datetime.fromtimestamp(last_refresh, tz=datetime.UTC).isoformat()
                if last_refresh is not None
                else None
            ),
            "error": get_last_refresh_error(),
        },
    )


@mcp.custom_route("/refresh/now", methods=["POST"])
async def refresh_now(_request: Request) -> JSONResponse:
    """Wake the refresh daemon so it refreshes the ledger replica immediately."""
    trigger_refresh_now()
    return JSONResponse({"triggered": True})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=MCP_PORT)
    args = parser.parse_args()
    start_refresh_daemon()
    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
