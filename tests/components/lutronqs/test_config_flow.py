"""Test the Lutron QS config flow."""

from unittest.mock import AsyncMock

import pytest

from homeassistant import config_entries
from homeassistant.components.lutronqs.config_flow import CannotConnect, InvalidAuth
from homeassistant.components.lutronqs.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from tests.common import MockConfigEntry


async def test_form(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_validate_connection: AsyncMock,
) -> None:
    """Test we get the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )
    await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Lutron QS (192.168.1.100)"
    assert result2["data"] == {
        CONF_HOST: "192.168.1.100",
        CONF_USERNAME: "nwk2",
        CONF_PASSWORD: "",
    }
    assert len(mock_setup_entry.mock_calls) == 1
    mock_validate_connection.assert_called_once_with("192.168.1.100", "nwk2", "")


async def test_form_with_password(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_validate_connection: AsyncMock,
) -> None:
    """Test form with a password."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.200",
            CONF_USERNAME: "custom_user",
            CONF_PASSWORD: "secret_password",
        },
    )
    await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Lutron QS (192.168.1.200)"
    assert result2["data"] == {
        CONF_HOST: "192.168.1.200",
        CONF_USERNAME: "custom_user",
        CONF_PASSWORD: "secret_password",
    }
    assert len(mock_setup_entry.mock_calls) == 1
    mock_validate_connection.assert_called_once_with(
        "192.168.1.200", "custom_user", "secret_password"
    )


async def test_form_cannot_connect(
    hass: HomeAssistant, mock_validate_connection: AsyncMock
) -> None:
    """Test we handle cannot connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    mock_validate_connection.side_effect = CannotConnect

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


async def test_form_invalid_auth(
    hass: HomeAssistant, mock_validate_connection: AsyncMock
) -> None:
    """Test we handle invalid auth error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    mock_validate_connection.side_effect = InvalidAuth

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "wrong",
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_form_unknown_error(
    hass: HomeAssistant, mock_validate_connection: AsyncMock
) -> None:
    """Test we handle unknown error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    mock_validate_connection.side_effect = Exception("Unexpected error")

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "unknown"}


async def test_form_already_configured(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_connection: AsyncMock,
) -> None:
    """Test we handle duplicate configuration."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_reconfigure_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test reconfigure flow."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": mock_config_entry.entry_id,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    # Check that form is pre-filled with current values
    assert result["data_schema"]({}) == {
        CONF_HOST: "192.168.1.100",
        CONF_USERNAME: "nwk2",
        CONF_PASSWORD: "",
    }

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.200",
            CONF_USERNAME: "new_user",
            CONF_PASSWORD: "new_password",
        },
    )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"

    # Verify the entry was updated
    assert mock_config_entry.data == {
        CONF_HOST: "192.168.1.200",
        CONF_USERNAME: "new_user",
        CONF_PASSWORD: "new_password",
    }
    assert mock_config_entry.title == "Lutron QS (192.168.1.200)"


async def test_reconfigure_flow_unchanged(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test reconfigure flow with unchanged credentials."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": mock_config_entry.entry_id,
        },
    )

    # Submit the same values (no changes)
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "",
        },
    )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"

    # Verify data remains the same
    assert mock_config_entry.data == {
        CONF_HOST: "192.168.1.100",
        CONF_USERNAME: "nwk2",
        CONF_PASSWORD: "",
    }


async def test_reauth_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
    mock_validate_connection: AsyncMock,
) -> None:
    """Test reauth flow."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": mock_config_entry.entry_id,
        },
        data=mock_config_entry.data,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Verify the form shows the host in description
    assert result["description_placeholders"]["host"] == "192.168.1.100"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "new_username",
            CONF_PASSWORD: "new_password",
        },
    )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"

    # Verify the entry was updated with new credentials
    assert mock_config_entry.data == {
        CONF_HOST: "192.168.1.100",
        CONF_USERNAME: "new_username",
        CONF_PASSWORD: "new_password",
    }
    mock_validate_connection.assert_called_once_with(
        "192.168.1.100", "new_username", "new_password"
    )


async def test_reauth_flow_cannot_connect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_connection: AsyncMock,
) -> None:
    """Test reauth flow with connection error."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": mock_config_entry.entry_id,
        },
        data=mock_config_entry.data,
    )

    mock_validate_connection.side_effect = CannotConnect

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "password",
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_invalid_auth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_validate_connection: AsyncMock,
) -> None:
    """Test reauth flow with invalid auth."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": mock_config_entry.entry_id,
        },
        data=mock_config_entry.data,
    )

    mock_validate_connection.side_effect = InvalidAuth

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "nwk2",
            CONF_PASSWORD: "wrong_password",
        },
    )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}
