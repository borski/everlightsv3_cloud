"""Config flow."""
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.everlightsv3_cloud.const import DOMAIN

from .conftest import PASSWORD, USERNAME


async def submit(hass, username=USERNAME, password=PASSWORD):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: username, CONF_PASSWORD: password}
    )


async def test_user_flow(hass, cloud):
    result = await submit(hass, username=f"  {USERNAME} ")
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    assert result["result"].unique_id == USERNAME


async def test_wrong_password_then_fixed(hass, cloud):
    result = await submit(hass, password="nope")
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_cloud_down(hass, cloud):
    cloud.queue("token", TimeoutError)
    assert (await submit(hass))["errors"] == {"base": "cannot_connect"}


async def test_unexpected_answer(hass, cloud):
    cloud.queue("bridges", (200, {"not": "a list"}))
    assert (await submit(hass))["errors"] == {"base": "unknown"}


async def test_no_bridges(hass, cloud):
    cloud.bridges.clear()
    assert (await submit(hass))["errors"] == {"base": "no_bridges"}


async def test_duplicate(hass, cloud, setup_entry):
    result = await submit(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass, cloud, setup_entry):
    cloud.password = "new-password"
    result = await setup_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "typo"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert setup_entry.data[CONF_PASSWORD] == "new-password"
