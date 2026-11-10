"""Real-HA tests for jellyfin_cast.next_track / previous_track, against the fake Chromecast's queue."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_get_all_descriptions
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from test_jellyfin_cast import CAST, TOKEN, setup
from test_play_by_name import BASE, SHOW, items

EPISODES = [{"Id": f"{n:032x}", "Name": f"Episode {n}", "Type": "Episode", "SeriesName": "Futurama",
             "ParentIndexNumber": 2, "IndexNumber": n} for n in range(1, 4)]


async def call(hass: HomeAssistant, service: str, **data: Any) -> dict:
    return await hass.services.async_call("jellyfin_cast", service, {"entity_id": CAST, **data},
                                          blocking=True, return_response=True)


async def shuffle(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, **extra: Any) -> Any:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Futurama", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Items?parentId={SHOW['Id']}", json=items(*EPISODES))
    for episode in EPISODES:
        aioclient_mock.get(f"{BASE}/Items?ids={episode['Id']}", json=items(episode))
    cast = await setup(hass)
    answer = await call(hass, "play_item", name="Futurama", shuffle=True, **extra)
    assert answer["ok"] is True, answer
    return cast


async def test_next_and_previous_move_through_the_queue(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    cast = await shuffle(hass, aioclient_mock)
    queue = cast.queue
    answer = await call(hass, "next_track")
    assert answer["ok"] is True and answer["item_id"] == queue[1], answer
    # What it skipped to, looked up on the server.
    assert answer["name"].startswith("Episode ") and answer["series"] == "Futurama" and answer["season"] == 2
    assert TOKEN not in answer["content_id"]
    namespace, message = cast.sent[-1]
    assert namespace == "urn:x-cast:com.connectsdk" and message["command"] == "NextTrack"
    assert message["accessToken"] == TOKEN and message["serverAddress"] == "http://jf.local:8096"
    assert message["receiverName"] == "Bedroom Chromecast Jellyfin"  # the name it was cast with
    answer = await call(hass, "previous_track")
    assert answer["ok"] is True and answer["item_id"] == queue[0], answer
    assert cast.launched == ["F007D354"]  # launched once, for the play; never for a skip


async def test_nothing_before_the_first_item(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await shuffle(hass, aioclient_mock)
    answer = await call(hass, "previous_track", timeout=2)
    assert answer["ok"] is False and answer["error"] == "Nothing to skip to: still on the same item after 2 s"


async def test_skip_keeps_the_receiver_name(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    cast = await shuffle(hass, aioclient_mock, receiver_name="Den TV")
    assert (await call(hass, "next_track"))["ok"] is True
    assert cast.sent[-1][1]["receiverName"] == "Den TV"


async def test_skip_needs_the_receiver_on_screen(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    cast = await setup(hass)  # nothing cast yet: another app (or none) is on the TV
    answer = await call(hass, "next_track")
    assert answer == {"entity_id": CAST, "ok": False, "error": "The Jellyfin receiver isn't playing on this TV"}
    assert cast.sent == [] and cast.launched == []


async def test_skip_details_are_best_effort(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Futurama", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Items?parentId={SHOW['Id']}", json=items(*EPISODES))
    aioclient_mock.get(f"{BASE}/Items?ids=", status=500)  # the details lookup fails
    cast = await setup(hass)
    assert (await call(hass, "play_item", name="Futurama", shuffle=True))["ok"] is True
    answer = await call(hass, "next_track")
    assert answer["ok"] is True and answer["item_id"] == cast.queue[1] and "name" not in answer, answer


async def test_skip_not_connected(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await setup(hass, connected=False)
    assert await call(hass, "next_track") == {"entity_id": CAST, "ok": False, "error": f"{CAST} is not connected"}


async def test_skip_rejects_non_cast_players(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("jellyfin_cast", "next_track", {"entity_id": "media_player.some_speaker"},
                                       blocking=True, return_response=True)


async def test_skip_without_response(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await shuffle(hass, aioclient_mock)
    assert await hass.services.async_call("jellyfin_cast", "next_track", {"entity_id": CAST}, blocking=True) is None


async def test_skip_actions_are_described(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create("media_player", "cast", "tv", suggested_object_id="living_room_tv")
    await setup(hass)
    descriptions = (await async_get_all_descriptions(hass))["jellyfin_cast"]
    for service in ("next_track", "previous_track"):
        picker = descriptions[service]["fields"]["entity_id"]["selector"]["entity"]
        assert picker["include_entities"] == ["media_player.living_room_tv"]
        assert descriptions[service]["response"] == {"optional": True}
        assert descriptions[service]["fields"]["timeout"]["default"] == 15
