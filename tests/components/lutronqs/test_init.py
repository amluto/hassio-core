"""Tests for Lutron QS connection handling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from lutron_integration import devices as lutron_devices, qse
from lutron_integration.connection import DisconnectedError, LoginError
from lutron_integration.recorded_session import SessionEvent
from lutron_integration.types import DeviceAction, IntegrationIDMap, SerialNumber

from homeassistant import config_entries
from homeassistant.components.lutronqs import (
    LutronQSData,
    _log_traffic,
    _mark_all_entities_unavailable,
    connection_monitor,
    monitor_unsolicited_messages,
    process_device_updates,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.common import MockConfigEntry


def _make_device_details(sn: SerialNumber) -> qse.DeviceDetails:
    return qse.DeviceDetails(
        sn=sn,
        integration_id=b"Device",
        family=b"KEYPAD(1)",
        product=b"QSWS2-GB(1)",
        raw_attrs={},
    )


async def test_monitor_unsolicited_messages_signals_disconnect(
    hass: HomeAssistant,
) -> None:
    """Test unsolicited monitor signals disconnect to the reconnect loop."""
    config_entry = MockConfigEntry(
        domain="lutronqs",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )
    conn = AsyncMock()
    conn.read_unsolicited.side_effect = DisconnectedError()
    config_entry.runtime_data = LutronQSData(
        connection=conn,
        universe=qse.LutronUniverse(),
    )

    await monitor_unsolicited_messages(hass, config_entry)

    assert config_entry.runtime_data.connection_lost_event.is_set()


def test_mark_all_entities_unavailable_clears_universe() -> None:
    """Test disconnect handling marks all entities unavailable immediately."""
    serial = SerialNumber(b"12345678")
    entity = MagicMock()
    runtime_data = LutronQSData(
        connection=AsyncMock(),
        universe=qse.LutronUniverse(
            devices_by_sn={serial: _make_device_details(serial)},
            iidmap=IntegrationIDMap(),
        ),
        entities_by_device={serial: [entity]},
    )

    config_entry = MockConfigEntry(domain="lutronqs", data={})
    config_entry.runtime_data = runtime_data

    _mark_all_entities_unavailable(config_entry)

    assert config_entry.runtime_data.universe.devices_by_sn == {}
    entity.async_write_ha_state.assert_called_once()


async def test_process_device_updates_restores_registered_devices(
    hass: HomeAssistant,
) -> None:
    """Test reconnect from an empty universe marks known devices available again."""
    serial = SerialNumber(b"12345678")
    conn = AsyncMock()
    entity = MagicMock()
    handler = MagicMock()
    update = lutron_devices.DeviceUpdate(
        serial_number=serial,
        component=1,
        action=DeviceAction.LIGHT_LEVEL,
        value=(b"100.00",),
    )

    config_entry = MockConfigEntry(
        domain="lutronqs",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )
    config_entry.runtime_data = LutronQSData(
        connection=conn,
        universe=qse.LutronUniverse(),
        entity_routing_table={(serial, 1, DeviceAction.LIGHT_LEVEL): handler},
        entities_by_device={serial: [entity]},
        registered_devices={serial},
    )

    with (
        patch(
            "homeassistant.components.lutronqs.qse.enumerate_universe",
            AsyncMock(
                return_value=qse.LutronUniverse(
                    devices_by_sn={serial: _make_device_details(serial)},
                    iidmap=IntegrationIDMap(),
                )
            ),
        ),
        patch(
            "homeassistant.components.lutronqs.lutron_devices.probe_device",
            AsyncMock(return_value=[update]),
        ),
    ):
        await process_device_updates(hass, config_entry)

    handler.assert_called_once_with(update)
    entity.async_write_ha_state.assert_called_once()


async def test_connection_monitor_reconnects_and_restarts_unsolicited(
    hass: HomeAssistant,
) -> None:
    """Test reconnect path clears disconnect state and restarts monitor."""
    old_conn = AsyncMock()
    new_conn = AsyncMock()
    config_entry = MockConfigEntry(
        domain="lutronqs",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )
    entity = MagicMock()
    serial = SerialNumber(b"12345678")
    config_entry.runtime_data = LutronQSData(
        connection=old_conn,
        universe=qse.LutronUniverse(
            devices_by_sn={serial: _make_device_details(serial)},
            iidmap=IntegrationIDMap(),
        ),
        entities_by_device={serial: [entity]},
    )
    config_entry.runtime_data.connection_lost_event.set()

    reconnected = asyncio.Event()

    async def fake_process_device_updates(
        _hass: HomeAssistant, entry: MockConfigEntry
    ) -> None:
        if entry.runtime_data.connection is new_conn:
            reconnected.set()

    with (
        patch(
            "homeassistant.components.lutronqs._open_logged_connection",
            AsyncMock(return_value=(AsyncMock(), AsyncMock())),
        ),
        patch(
            "homeassistant.components.lutronqs.lutron_connection.login",
            AsyncMock(return_value=new_conn),
        ),
        patch(
            "homeassistant.components.lutronqs.process_device_updates",
            side_effect=fake_process_device_updates,
        ),
        patch("homeassistant.components.lutronqs._start_unsolicited_monitor") as restart,
        patch("homeassistant.components.lutronqs.asyncio.sleep", AsyncMock()),
    ):
        task = hass.async_create_task(connection_monitor(hass, config_entry))
        try:
            await reconnected.wait()
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass

    old_conn.disconnect.assert_awaited_once()
    restart.assert_called_once_with(hass, config_entry)
    entity.async_write_ha_state.assert_called_once()
    assert config_entry.runtime_data.connection is new_conn
    assert not config_entry.runtime_data.connection_lost_event.is_set()


async def test_connection_monitor_starts_reauth_on_reconnect_login_error(
    hass: HomeAssistant,
) -> None:
    """Test reconnect auth failures start reauth and stop cleanly."""
    old_conn = AsyncMock()
    config_entry = MockConfigEntry(
        domain="lutronqs",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "bad_password",
        },
    )
    entity = MagicMock()
    serial = SerialNumber(b"12345678")
    config_entry.runtime_data = LutronQSData(
        connection=old_conn,
        universe=qse.LutronUniverse(
            devices_by_sn={serial: _make_device_details(serial)},
            iidmap=IntegrationIDMap(),
        ),
        entities_by_device={serial: [entity]},
    )
    config_entry.runtime_data.connection_lost_event.set()

    with (
        patch(
            "homeassistant.components.lutronqs._open_logged_connection",
            AsyncMock(return_value=(AsyncMock(), AsyncMock())),
        ),
        patch(
            "homeassistant.components.lutronqs.lutron_connection.login",
            AsyncMock(side_effect=LoginError()),
        ),
        patch("homeassistant.components.lutronqs.asyncio.sleep", AsyncMock()),
        patch.object(config_entry, "async_start_reauth") as start_reauth,
    ):
        task = config_entry.async_create_background_task(
            hass,
            connection_monitor(hass, config_entry),
            "lutronqs-test-connection-monitor",
        )
        await asyncio.wait_for(task, timeout=1)

    old_conn.disconnect.assert_awaited_once()
    start_reauth.assert_called_once_with(hass)
    entity.async_write_ha_state.assert_called_once()
    assert config_entry.runtime_data.connection is old_conn
    assert config_entry.runtime_data.connection_lost_event.is_set()
    assert task.exception() is None


async def test_component_recovers_after_connection_monitor_auth_failure(
    hass: HomeAssistant,
) -> None:
    """Test the entry reloads cleanly after reconnect auth failure triggers reauth."""
    config_entry = MockConfigEntry(
        domain="lutronqs",
        title="Lutron QS (192.168.1.100)",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "bad_password",
        },
        unique_id="192.168.1.100",
    )
    config_entry.add_to_hass(hass)

    first_conn = AsyncMock()
    second_conn = AsyncMock()
    first_conn.read_unsolicited.side_effect = DisconnectedError()

    wait_for_cancel = asyncio.Event()

    async def _block_unsolicited() -> bytes:
        await wait_for_cancel.wait()
        return b""

    second_conn.read_unsolicited.side_effect = _block_unsolicited

    open_connection = AsyncMock(
        side_effect=[
            (AsyncMock(), AsyncMock()),
            (AsyncMock(), AsyncMock()),
            (AsyncMock(), AsyncMock()),
        ]
    )
    login = AsyncMock(side_effect=[first_conn, LoginError(), second_conn])

    with (
        patch(
            "homeassistant.components.lutronqs._open_logged_connection",
            open_connection,
        ),
        patch("homeassistant.components.lutronqs.lutron_connection.login", login),
        patch(
            "homeassistant.components.lutronqs.qse.enumerate_universe",
            AsyncMock(return_value=qse.LutronUniverse()),
        ),
        patch("homeassistant.components.lutronqs.asyncio.sleep", AsyncMock()),
        patch(
            "homeassistant.components.lutronqs.config_flow.validate_connection",
            AsyncMock(return_value=None),
        ) as validate_connection,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        assert config_entry.state is config_entries.ConfigEntryState.LOADED

        flows = hass.config_entries.flow.async_progress()
        assert len(flows) == 1
        assert flows[0]["context"]["entry_id"] == config_entry.entry_id
        assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH

        result = await hass.config_entries.flow.async_configure(
            flows[0]["flow_id"],
            {
                CONF_USERNAME: "nwk2",
                CONF_PASSWORD: "good_password",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert validate_connection.await_count == 1
    assert config_entry.data[CONF_PASSWORD] == "good_password"
    assert config_entry.state is config_entries.ConfigEntryState.LOADED
    assert config_entry.runtime_data.connection is second_conn
    assert not config_entry.runtime_data.connection_lost_event.is_set()
    assert open_connection.await_count == 3
    assert login.await_count == 3


def test_log_traffic_logs_direction_and_redaction(caplog) -> None:
    """Test raw traffic logging includes direction and redaction marker."""
    caplog.set_level("DEBUG")

    _log_traffic(
        "192.168.1.100",
        SessionEvent(direction="outgoing", contents=b"abc\r\n", is_redacted=True),
    )
    _log_traffic(
        "192.168.1.100",
        SessionEvent(direction="incoming", contents=b"~MONITORING,1,1\r\n"),
    )

    assert "Traffic 192.168.1.100 outgoing (redacted): b'abc\\r\\n'" in caplog.text
    assert "Traffic 192.168.1.100 incoming: b'~MONITORING,1,1\\r\\n'" in caplog.text
