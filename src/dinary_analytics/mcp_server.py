"""MCP server exposing ledger query and schema tools."""

import argparse
import json

from mcp.server.fastmcp import FastMCP

from dinary_analytics.connection import LEDGER_SCHEMA, open_ledger
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
    con = open_ledger()
    try:
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return json.dumps([dict(zip(columns, row, strict=True)) for row in rows], default=str)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="dinary-analytics MCP server")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Run as SSE HTTP server on this port (default: stdio transport)",
    )
    args = parser.parse_args()
    if args.port is not None:
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
