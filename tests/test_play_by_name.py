"""Real-HA tests for jellyfin_cast.play_item with a name: the lookup against a mocked Jellyfin API."""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from test_jellyfin_cast import CAST, DASHED, ITEM, TOKEN, setup

BASE = "http://jf.local:8096"
MOVIE = {"Id": DASHED, "Name": "The Matrix", "Type": "Movie", "ProductionYear": 1999}
SHOW = {"Id": "5e1e5000-0000-0000-0000-000000000001", "Name": "Futurama", "Type": "Series", "ProductionYear": 1999}
EPISODE = {"Id": DASHED, "Name": "Space Pilot 3000", "Type": "Episode", "SeriesName": "Futurama",
           "ParentIndexNumber": 1, "IndexNumber": 1, "ProductionYear": 1999}


def items(*found: dict) -> dict:
    return {"Items": list(found), "TotalRecordCount": len(found)}


async def play(hass: HomeAssistant, **data: Any) -> dict:
    return await hass.services.async_call("jellyfin_cast", "play_item", {"entity_id": CAST, **data},
                                          blocking=True, return_response=True)


def played(cast: Any) -> dict:
    """The one item the TV was told to play."""
    assert len(cast.sent) == 1, cast.sent
    return cast.sent[0][1]["options"]["items"][0]


def query(aioclient_mock: AiohttpClientMocker, index: int) -> dict:
    url = aioclient_mock.mock_calls[index][1]
    return dict(url.query)


async def test_movie_by_exact_name(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=the%20matrix", json=items(
        {"Id": "f" * 32, "Name": "The Matrix Reloaded", "Type": "Movie"}, MOVIE))
    cast = await setup(hass)
    answer = await play(hass, name="the matrix")
    assert answer["ok"] is True, answer
    assert answer["query"] == "the matrix" and answer["item_id"] == ITEM
    assert answer["name"] == "The Matrix" and answer["type"] == "Movie" and answer["year"] == 1999
    assert answer["matched"] == "exact" and "other_matches" not in answer
    assert played(cast) == {"Id": ITEM, "Type": "Movie", "MediaType": "Video", "IsFolder": False}
    q = query(aioclient_mock, 0)
    assert q["userId"] == "u1" and q["includeItemTypes"] == "Movie,Series,Episode,Video" and q["recursive"] == "true"
    headers = aioclient_mock.mock_calls[0][3]
    assert headers["Authorization"] == f'MediaBrowser Token="{TOKEN}"'


async def test_show_plays_next_up(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=futurama", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Shows/NextUp", json=items(EPISODE))
    cast = await setup(hass)
    answer = await play(hass, name="futurama")
    assert answer["ok"] is True, answer
    assert answer["type"] == "Episode" and answer["series"] == "Futurama"
    assert (answer["season"], answer["episode"]) == (1, 1)
    assert answer["series_id"] == "5e1e5000000000000000000000000001" and answer["episode_choice"] == "next_up"
    assert played(cast)["Type"] == "Episode" and played(cast)["Id"] == ITEM
    assert query(aioclient_mock, 1)["seriesId"] == SHOW["Id"]


async def test_show_random_episode(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Futurama", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Items?sortBy=Random", json=items(EPISODE))
    cast = await setup(hass)
    answer = await play(hass, name="Futurama", episode="random")
    assert answer["ok"] is True and answer["episode_choice"] == "random", answer
    q = query(aioclient_mock, 1)
    assert q["parentId"] == SHOW["Id"] and q["includeItemTypes"] == "Episode" and q["isMissing"] == "false"
    assert played(cast)["Type"] == "Episode"


async def test_show_watched_to_the_end_starts_over(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    special = {**EPISODE, "Id": "0" * 32, "Name": "Special", "ParentIndexNumber": 0}
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Futurama", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Shows/NextUp", json=items())
    aioclient_mock.get(f"{BASE}/Items?sortBy=ParentIndexNumber,IndexNumber", json=items(special, EPISODE))
    await setup(hass)
    answer = await play(hass, name="Futurama")
    assert answer["ok"] is True and answer["name"] == "Space Pilot 3000", answer


async def test_same_name_twice(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fargo_show = {"Id": "a" * 32, "Name": "Fargo", "Type": "Series", "ProductionYear": 2014}
    fargo_movie = {"Id": DASHED, "Name": "Fargo", "Type": "Movie", "ProductionYear": 1996}
    episode_named_fargo = {"Id": "b" * 32, "Name": "Fargo", "Type": "Episode", "SeriesName": "Other"}
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Fargo", json=items(episode_named_fargo, fargo_show, fargo_movie))
    aioclient_mock.get(f"{BASE}/Shows/NextUp", json=items(EPISODE))
    await setup(hass)
    answer = await play(hass, name="Fargo")
    # A show or a movie before an episode, then the server's order; the others are listed.
    assert answer["ok"] is True and answer["series_id"] == "a" * 32, answer
    assert [(m["type"], m["item_id"]) for m in answer["other_matches"]] == [("Movie", ITEM), ("Episode", "b" * 32)]


async def test_type_filter_any_case(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Fargo", json=items({"Id": DASHED, "Name": "Fargo", "Type": "Movie"}))
    await setup(hass)
    answer = await play(hass, name="Fargo", item_type="movie")
    assert answer["ok"] is True and answer["type"] == "Movie", answer
    assert query(aioclient_mock, 0)["includeItemTypes"] == "Movie"


async def test_partial_match_skips_episodes(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    episode = {"Id": "c" * 32, "Name": "Postman and Ground's Lava", "Type": "Episode"}
    video = {"Id": DASHED, "Name": "Stylized Lava Swirl (1080p)", "Type": "Video"}
    aioclient_mock.get(f"{BASE}/Items?searchTerm=lava", json=items(episode, {"Id": "d" * 32, "Name": "L'Avant Dernier",
                                                                           "Type": "Movie"}, video))
    aioclient_mock.get(f"{BASE}/Items?nameStartsWith=lava", json=items())
    cast = await setup(hass)
    answer = await play(hass, name="lava")
    assert answer["ok"] is True and answer["matched"] == "partial" and answer["type"] == "Video", answer
    assert played(cast)["Type"] == "Video"


async def test_search_miss_falls_back_to_name_start(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    # The search plugin hasn't indexed it yet; the database knows it by its sort name (no "The").
    aioclient_mock.get(f"{BASE}/Items?searchTerm=The%20Matrix", json=items({"Id": "e" * 32, "Name": "Rain Man",
                                                                           "Type": "Movie"}))
    aioclient_mock.get(f"{BASE}/Items?nameStartsWith=Matrix", json=items(MOVIE))
    await setup(hass)
    answer = await play(hass, name="The Matrix")
    assert answer["ok"] is True and answer["item_id"] == ITEM and answer["matched"] == "exact", answer


async def test_nothing_found(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=zzqqxx", json=items({"Id": "e" * 32, "Name": "Rain Man",
                                                                     "Type": "Movie"}))
    aioclient_mock.get(f"{BASE}/Items?nameStartsWith=zzqqxx", json=items())
    cast = await setup(hass)
    answer = await play(hass, name="zzqqxx")
    assert answer == {"entity_id": CAST, "query": "zzqqxx", "ok": False,
                      "error": "Nothing in Jellyfin is called 'zzqqxx'", "candidates": ["Rain Man"]}
    assert cast.sent == [] and cast.launched == []


async def test_token_refused(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items", status=401)
    cast = await setup(hass)
    answer = await play(hass, name="Futurama")
    assert answer["ok"] is False and answer["error"] == "Jellyfin lookup failed: HTTP 401 (the token was refused)"
    assert cast.sent == []


async def test_server_unreachable(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items", exc=TimeoutError())
    cast = await setup(hass)
    answer = await play(hass, name="Futurama")
    assert answer["ok"] is False and answer["error"].startswith("Jellyfin lookup failed:"), answer
    assert cast.sent == []


async def test_not_connected_skips_the_lookup(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await setup(hass, connected=False)
    answer = await play(hass, name="Futurama")
    assert answer == {"entity_id": CAST, "query": "Futurama", "ok": False, "error": f"{CAST} is not connected"}
    assert aioclient_mock.call_count == 0


async def test_unsearchable_type(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await setup(hass)
    with pytest.raises(ServiceValidationError):
        await play(hass, name="Futurama", item_type="Season")
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize("data", [{}, {"item_id": ITEM, "name": "Futurama"}, {"name": "x", "episode": "last"}])
async def test_schema(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, data: dict) -> None:
    await setup(hass)
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await play(hass, **data)


async def test_item_id_calls_do_not_search(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    cast = await setup(hass)
    answer = await play(hass, item_id=DASHED)
    assert answer["ok"] is True and "query" not in answer and "name" not in answer
    assert played(cast)["Type"] == "Video"  # unchanged default
    assert aioclient_mock.call_count == 0


VIEWS = items({"Id": "11111111111111111111111111111111", "Name": "Movies", "Type": "CollectionFolder"},
              {"Id": "22222222222222222222222222222222", "Name": "TV Shows", "Type": "CollectionFolder"})


async def test_library_narrows_the_search(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fargo_show = {"Id": "a" * 32, "Name": "Fargo", "Type": "Series", "ProductionYear": 2014}
    aioclient_mock.get(f"{BASE}/UserViews", json=VIEWS)
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Fargo&parentId=22222222222222222222222222222222", json=items(fargo_show))
    aioclient_mock.get(f"{BASE}/Shows/NextUp", json=items(EPISODE))
    cast = await setup(hass)
    answer = await play(hass, name="Fargo", library="tv shows")  # names compare like item names
    assert answer["ok"] is True and answer["library"] == "TV Shows", answer
    assert answer["series_id"] == "a" * 32 and "other_matches" not in answer
    assert played(cast)["Type"] == "Episode"
    assert query(aioclient_mock, 0)["userId"] == "u1"


async def test_library_also_scopes_the_fallback(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/UserViews", json=VIEWS)
    aioclient_mock.get(f"{BASE}/Items?searchTerm=The%20Matrix", json=items())
    aioclient_mock.get(f"{BASE}/Items?nameStartsWith=Matrix&parentId=11111111111111111111111111111111",
                       json=items(MOVIE))
    await setup(hass)
    answer = await play(hass, name="The Matrix", library="Movies")
    assert answer["ok"] is True and answer["item_id"] == ITEM, answer
    assert query(aioclient_mock, 1)["parentId"] == "11111111111111111111111111111111"


async def test_unknown_library(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/UserViews", json=VIEWS)
    cast = await setup(hass)
    answer = await play(hass, name="Fargo", library="Anime")
    assert answer == {"entity_id": CAST, "query": "Fargo", "ok": False,
                      "error": "No library called 'Anime'", "candidates": ["Movies", "TV Shows"]}
    assert aioclient_mock.call_count == 1 and cast.sent == []


async def test_nothing_in_that_library(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/UserViews", json=VIEWS)
    aioclient_mock.get(f"{BASE}/Items", json=items())
    await setup(hass)
    answer = await play(hass, name="Futurama", library="Movies")
    assert answer["ok"] is False and answer["error"] == "Nothing in Movies is called 'Futurama'", answer


async def test_library_needs_a_name(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await setup(hass)
    with pytest.raises(ServiceValidationError):
        await play(hass, item_id=ITEM, library="Movies")
    assert aioclient_mock.call_count == 0


# ---- the UI's TV picker ------------------------------------------------------------------

from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers.service import async_get_all_descriptions  # noqa: E402


async def tv_picker(hass: HomeAssistant) -> dict:
    descriptions = await async_get_all_descriptions(hass)
    return descriptions["jellyfin_cast"]["play_item"]["fields"]["entity_id"]["selector"]["entity"]


async def test_tv_picker_lists_only_video_cast_players(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    reg = er.async_get(hass)
    reg.async_get_or_create("media_player", "cast", "tv", suggested_object_id="living_room_tv")
    reg.async_get_or_create("media_player", "cast", "hub", suggested_object_id="kitchen_hub")
    reg.async_get_or_create("media_player", "cast", "mini", suggested_object_id="bathroom_mini",
                            original_device_class="speaker")
    reg.async_get_or_create("media_player", "cast", "group", suggested_object_id="all_speakers",
                            original_device_class="speaker")
    reg.async_get_or_create("media_player", "cast", "old", suggested_object_id="old_tv",
                            disabled_by=er.RegistryEntryDisabler.USER)
    reg.async_get_or_create("media_player", "sonos", "sonos", suggested_object_id="sonos_tv")
    await setup(hass)
    picker = await tv_picker(hass)
    assert picker["include_entities"] == ["media_player.kitchen_hub", "media_player.living_room_tv"]
    assert picker["filter"] == [{"domain": ["media_player"], "integration": "cast"}]
    # The rest of the description is still services.yaml's, and the action still answers.
    descriptions = await async_get_all_descriptions(hass)
    assert "name" in descriptions["jellyfin_cast"]["play_item"]["fields"]
    assert descriptions["jellyfin_cast"]["play_item"]["response"] == {"optional": True}

    # A new TV shows up; a speaker the user re-classified as a TV too.
    reg.async_get_or_create("media_player", "cast", "tv2", suggested_object_id="bedroom_tv")
    reg.async_update_entity("media_player.bathroom_mini", device_class="tv")
    await hass.async_block_till_done()
    assert (await tv_picker(hass))["include_entities"] == [
        "media_player.bathroom_mini", "media_player.bedroom_tv", "media_player.kitchen_hub", "media_player.living_room_tv"]


async def test_tv_picker_without_any_tv_keeps_the_cast_filter(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await setup(hass)
    picker = await tv_picker(hass)
    assert "include_entities" not in picker
    assert picker["filter"] == [{"domain": ["media_player"], "integration": "cast"}]


# ---- shuffle -------------------------------------------------------------------------------

import json  # noqa: E402

EPISODES = [{"Id": f"{n:032x}", "Name": f"Episode {n}", "Type": "Episode", "SeriesName": "Futurama",
             "ParentIndexNumber": 1, "IndexNumber": n} for n in range(1, 4)]


def queue_of(cast: Any) -> list[str]:
    assert len(cast.sent) == 1, cast.sent
    items = cast.sent[0][1]["options"]["items"]
    assert all(item["Type"] == "Episode" and item["MediaType"] == "Video" for item in items)
    return [item["Id"] for item in items]


async def test_shuffle_a_show_by_name(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Futurama", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Items?parentId={SHOW['Id']}", json=items(*EPISODES))
    cast = await setup(hass)
    answer = await play(hass, name="Futurama", shuffle=True)
    assert answer["ok"] is True, answer
    assert answer["episode_choice"] == "shuffle" and answer["queue_length"] == 200
    assert answer["series_id"] == "5e1e5000000000000000000000000001" and answer["series"] == "Futurama"
    queue = queue_of(cast)
    assert len(queue) == 200 and answer["item_id"] == queue[0]
    ids = [episode["Id"] for episode in EPISODES]
    # Every pass is a new random order of all the episodes, and no episode plays twice in a row.
    for start in range(0, 198, 3):
        assert sorted(queue[start:start + 3]) == sorted(ids)
    assert all(a != b for a, b in zip(queue, queue[1:]))
    # Next Up isn't asked: shuffle replaces the episode choice.
    assert not any("/Shows/NextUp" in str(call[1]) for call in aioclient_mock.mock_calls)
    q = query(aioclient_mock, 1)
    assert q["includeItemTypes"] == "Episode" and q["isMissing"] == "false"
    assert len(json.dumps(cast.sent[0][1])) < 60000  # a cast message is limited to 64 kB


async def test_shuffle_a_long_show(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    many = [{"Id": f"{n:032x}", "Name": f"E{n}", "Type": "Episode"} for n in range(1, 751)]
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Simpsons", json=items({**SHOW, "Name": "Simpsons"}))
    aioclient_mock.get(f"{BASE}/Items?parentId={SHOW['Id']}", json=items(*many))
    cast = await setup(hass)
    answer = await play(hass, name="Simpsons", shuffle=True)
    assert answer["ok"] is True, answer
    queue = queue_of(cast)
    assert len(queue) == 200 and len(set(queue)) == 200  # 200 different episodes


async def test_shuffle_a_show_by_id(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?ids=5e1e5000000000000000000000000001", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Items?parentId={SHOW['Id']}", json=items(*EPISODES))
    cast = await setup(hass)
    answer = await play(hass, item_id=SHOW["Id"], shuffle=True)
    assert answer["ok"] is True and answer["episode_choice"] == "shuffle", answer
    assert len(queue_of(cast)) == 200 and answer["item_id"] == queue_of(cast)[0]


async def test_shuffle_ignored_for_a_movie(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=The%20Matrix", json=items(MOVIE))
    cast = await setup(hass)
    answer = await play(hass, name="The Matrix", shuffle=True)
    assert answer["ok"] is True and "episode_choice" not in answer and "queue_length" not in answer, answer
    assert played(cast) == {"Id": ITEM, "Type": "Movie", "MediaType": "Video", "IsFolder": False}


async def test_shuffle_with_a_movie_id_keeps_the_given_type(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?ids={ITEM}", json=items(MOVIE))
    cast = await setup(hass)
    answer = await play(hass, item_id=DASHED, item_type="Episode", shuffle=True)
    assert answer["ok"] is True, answer
    assert played(cast)["Type"] == "Episode"  # the caller's item_type still wins for an id


async def test_shuffle_a_show_without_episodes(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items?searchTerm=Futurama", json=items(SHOW))
    aioclient_mock.get(f"{BASE}/Items?parentId={SHOW['Id']}", json=items())
    cast = await setup(hass)
    answer = await play(hass, name="Futurama", shuffle=True)
    assert answer["ok"] is False and answer["error"] == "'Futurama' has no episode to play", answer
    assert cast.sent == []


async def test_shuffle_with_an_unknown_id(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/Items", json=items())
    cast = await setup(hass)
    answer = await play(hass, item_id=ITEM, shuffle=True)
    assert answer["ok"] is False and "No item with id" in answer["error"] and cast.sent == []
