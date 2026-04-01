from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_toolcall_log():
    """Mock toolcall_log globally so tests don't need HTTP request context."""
    with patch("togo_mcp.server.toolcall_log"):
        yield
