"""Client behaviour: auth lifecycle, retries, and the never-replay-writes rule."""
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.everlightsv3_cloud.api import (
    EverlightsCloudAuthenticationError,
    EverlightsCloudClient,
    EverlightsCloudCommandUnknownError,
    EverlightsCloudCommunicationError,
    EverlightsCloudError,
    EverlightsCloudRateLimitError,
)

from .conftest import BACK, BRIDGE, FRONT, GARAGE, PASSWORD, STRAY, USERNAME, load_fixture

RED = {"pattern": ["FF0000"], "effects": []}


@pytest.fixture
def client(hass, cloud):
    return EverlightsCloudClient(async_get_clientsession(hass), USERNAME, PASSWORD)


async def test_account_skips_zone_on_foreign_bridge(client, cloud):
    account = await client.async_get_account()
    assert list(account["bridges"]) == [BRIDGE]
    assert set(account["zones"]) == {GARAGE, FRONT, BACK}
    assert STRAY not in account["zones"]
    assert account["zones"][GARAGE]["alias"] == "Garage"


async def test_account_skips_unpaired(client, cloud):
    cloud.zones[1]["unpairedDate"] = "2026-01-01T00:00:00.000Z"
    assert len((await client.async_get_account())["zones"]) == 2


async def test_wrong_password(hass, cloud):
    client = EverlightsCloudClient(async_get_clientsession(hass), USERNAME, "wrong")
    with pytest.raises(EverlightsCloudAuthenticationError):
        await client.async_login()


async def test_token_reused_then_refreshed(client, cloud, frozen_now):
    await client.async_get_account()
    await client.async_get_account()
    assert cloud.count("POST", "token:password") == 1
    frozen_now.tick(86400)
    await client.async_get_account()
    assert cloud.count("POST", "token:refresh_token") == 1
    assert cloud.count("POST", "token:password") == 1
    # The refresh token rotates. The next refresh must use the new one.
    frozen_now.tick(86400)
    await client.async_get_account()
    assert cloud.count("POST", "token:refresh_token") == 2
    assert cloud.count("POST", "token:password") == 1


async def test_expired_refresh_token_falls_back_to_password(client, cloud, frozen_now):
    await client.async_get_account()
    cloud.refresh_tokens.clear()
    frozen_now.tick(86400)
    await client.async_get_account()
    assert cloud.count("POST", "token:password") == 2


async def test_expired_credentials(client, cloud, frozen_now):
    await client.async_get_account()
    cloud.refresh_tokens.clear()
    cloud.password = "changed-in-the-app"
    frozen_now.tick(86400)
    with pytest.raises(EverlightsCloudAuthenticationError):
        await client.async_get_account()


async def test_revoked_access_token_logs_in_once(client, cloud):
    await client.async_get_account()
    cloud.access_tokens.clear()  # Server answers 400 "Authorization failure".
    await client.async_get_account()
    assert cloud.count("POST", "token:password") == 2


async def test_remote_token_reminted_on_401(client, cloud):
    await client.async_get_bridge_status(BRIDGE)
    cloud.remote_tokens.clear()
    status = await client.async_get_bridge_status(BRIDGE)
    assert status["firmwareVersion"] == "B5010"
    assert cloud.count("GET", "remotetoken") == 2


async def test_read_retries_transient_errors(client, cloud):
    cloud.queue("status", 502, TimeoutError)
    assert (await client.async_get_bridge_status(BRIDGE))["serverReachable"] is True
    assert cloud.count("GET", "status") == 3


async def test_read_gives_up(client, cloud):
    cloud.queue("status", 504, 504, 504)
    with pytest.raises(EverlightsCloudCommunicationError):
        await client.async_get_bridge_status(BRIDGE)
    assert cloud.count("GET", "status") == 3


async def test_rate_limit_is_not_retried(client, cloud):
    cloud.queue("status", 429)
    with pytest.raises(EverlightsCloudRateLimitError) as err:
        await client.async_get_bridge_status(BRIDGE)
    assert err.value.retry_after == 30
    assert cloud.count("GET", "status") == 1


async def test_tunnel_gone_like_b5010_local(client, cloud):
    """If the vendor ever answers the tunnel the way B5010 answers the LAN."""
    gone = {"error": "gone", "cloud": True}
    cloud.queue("status", (410, gone))
    with pytest.raises(EverlightsCloudError):
        await client.async_get_bridge_status(BRIDGE)
    assert cloud.count("GET", "status") == 1
    # The B5010 local status document has the same shape the tunnel serves.
    assert {"serial", "firmwareVersion", "zones"} <= set(load_fixture("local_status_b5010"))


async def test_write_sent_once(client, cloud):
    await client.async_set_zone_sequence(BRIDGE, GARAGE, RED)
    assert cloud.posts == [(GARAGE, RED)]
    assert cloud.sequences[GARAGE] == RED


@pytest.mark.parametrize("failure", [TimeoutError, 502, 500, 429])
async def test_write_never_replayed(client, cloud, failure):
    await client.async_get_bridge_status(BRIDGE)
    cloud.queue("sequence", failure)
    expected = EverlightsCloudCommandUnknownError if failure is TimeoutError else EverlightsCloudError
    with pytest.raises(expected):
        await client.async_set_zone_sequence(BRIDGE, GARAGE, RED)
    assert len(cloud.posts) == 1


async def test_write_retried_only_after_auth_rejection(client, cloud):
    await client.async_get_bridge_status(BRIDGE)
    cloud.remote_tokens.clear()  # 401: rejected before reaching the bridge.
    await client.async_set_zone_sequence(BRIDGE, GARAGE, RED)
    assert len(cloud.posts) == 2
    assert cloud.sequences[GARAGE] == RED
