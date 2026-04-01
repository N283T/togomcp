import asyncio

import togo_mcp.api_tools as api_tools  # noqa: F401 (registers @mcp.tool handlers)
import togo_mcp.rdf_portal as rdf_portal  # noqa: F401 (registers @mcp.tool handlers)

from .ncbi_tools import ncbi_mcp
from .server import mcp
from .togoid import togoid_mcp


async def setup():
    mcp.mount(togoid_mcp, "togoid")
    mcp.mount(ncbi_mcp, "ncbi")


def run():
    asyncio.run(setup())
    mcp.run(transport="http", host="0.0.0.0", port=8000)


def run_local():
    asyncio.run(setup())
    mcp.run()


def run_admin():
    asyncio.run(setup())
    mcp.run()


if __name__ == "__main__":
    run()
