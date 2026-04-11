"""Common fixtures for Lutron QS tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.lutronqs.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from tests.common import MockConfigEntry


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with (
        patch(
            "homeassistant.components.lutronqs.async_setup_entry", return_value=True
        ) as mock_setup_entry,
        patch(
            "homeassistant.components.lutronqs.async_unload_entry", return_value=True
        ),
    ):
        yield mock_setup_entry


@pytest.fixture
def mock_validate_connection() -> Generator[AsyncMock]:
    """Mock successful connection validation."""
    with patch(
        "homeassistant.components.lutronqs.config_flow.validate_connection",
        return_value=None,
    ) as mock_validate:
        yield mock_validate


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
        unique_id="192.168.1.100",
    )


@pytest.fixture
def mock_lutron_connection() -> Generator[MagicMock]:
    """Mock the Lutron connection."""
    with (
        patch(
            "homeassistant.components.lutronqs._open_logged_connection",
        ) as mock_open_logged_connection,
        patch(
            "homeassistant.components.lutronqs.lutron_connection.login",
        ) as mock_login,
        patch(
            "homeassistant.components.lutronqs.qse.enumerate_universe",
        ) as mock_enumerate,
    ):
        # Mock the TCP connection
        mock_reader = AsyncMock()
        mock_writer = AsyncMock()
        mock_open_logged_connection.return_value = (mock_reader, mock_writer)

        # Mock the login
        mock_conn = AsyncMock()
        mock_conn.disconnect = AsyncMock()
        mock_conn.read_unsolicited = AsyncMock()
        mock_conn.raw_query = AsyncMock()
        mock_login.return_value = mock_conn

        # Mock the universe enumeration
        mock_universe = MagicMock()
        mock_universe.devices_by_sn = {}
        mock_enumerate.return_value = mock_universe

        yield {
            "open_connection": mock_open_logged_connection,
            "open_logged_connection": mock_open_logged_connection,
            "login": mock_login,
            "enumerate": mock_enumerate,
            "connection": mock_conn,
            "universe": mock_universe,
        }
