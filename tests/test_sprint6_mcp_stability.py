import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from services.cognitive.mcp.client import McpSession
from services.cognitive.mcp.presenter_manager import PresenterSessionManager


@pytest.mark.asyncio
async def test_presenter_sweep_idle():
    """Verify PresenterSessionManager.sweep_idle() executes cleanly."""
    mgr = PresenterSessionManager(server_commands={"ppt": ["python", "-m", "ppt_mcp"]})
    await mgr.sweep_idle()


@pytest.mark.asyncio
async def test_mcp_session_start_initialize_timeout():
    """Verify McpSession.start() raises RuntimeError when initialize() times out."""
    session = McpSession(["echo", "test"])

    mock_stdio_ctx = MagicMock()
    mock_stdio_ctx.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_stdio_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_client_session = MagicMock()
    mock_client_session.initialize = AsyncMock(side_effect=asyncio.TimeoutError())

    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_session)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.cognitive.mcp.client.stdio_client", return_value=mock_stdio_ctx), \
         patch("services.cognitive.mcp.client.ClientSession", return_value=mock_client_ctx):

        with pytest.raises(RuntimeError) as exc_info:
            await session.start()

        assert "MCP server did not respond in time" in str(exc_info.value)
        assert session._session is None
