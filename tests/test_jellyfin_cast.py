"""Real-HA tests for custom_components/jellyfin_cast, with a fake Chromecast behind a fake cast entity."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from pychromecast.controllers.media import MediaStatus

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.setup import async_setup_component

TOKEN = "0123456789abcdef0123456789abcdef"
CONFIG = {"jellyfin_cast": {"url": "http://jf.local:8096/", "user_id": "u1", "token": TOKEN}}
ITEM = "a6713298fae738f944bc7e06f642a419"
DASHED = "a6713298-fae7-38f9-44bc-7e06f642a419"
CAST = "media_player.bedroom_chromecast"


class FakeReceiverController:
    def __init__(self, cast: FakeChromecast) -> None:
        self.cast = cast
        self.app_id: str | None = None

    def launch_app(self, app_id: str, *, force_launch: bool = False, callback_function: Any = None) -> None:
        self.cast.launched.append(app_id)
        self.app_id = app_id
        self.cast.app_id = app_id
        self.cast.socket_client.app_namespaces = ["urn:x-cast:com.connectsdk", "urn:x-cast:com.google.cast.media"]
        if callback_function:
            callback_function(True, None)


class FakeSocketClient:
    def __init__(self, cast: FakeChromecast) -> None:
        self.cast = cast
        self.app_namespaces: list[str] = []
        self.receiver_controller = FakeReceiverController(cast)

    def send_app_message(self, namespace: str, data: dict, *, inc_session_id: bool = False,
                         callback_function: Any = None, no_add_request_id: bool = False) -> None:
        self.cast.sent.append((namespace, data))
        if callback_function:
            callback_function(True, None)
        threading.Timer(0.2, self.cast.answer, args=(data,)).start()


class FakeChromecast:
    uri = "fake:8009"

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.loads = 0
        self.app_id: str | None = None
        self.queue: list[str] = []
        self.index = 0
        self.sent: list[tuple[str, dict]] = []
        self.launched: list[str] = []
        self.socket_client = FakeSocketClient(self)
        self.listeners: list[Any] = []
        self.media_controller = SimpleNamespace(status=MediaStatus(), register_status_listener=self.listeners.append)
        # What was on screen before: another stream, already playing.
        self.media_controller.status.content_id = "http://other/stream.m3u8"
        self.media_controller.status.media_session_id = 3
        self.media_controller.status.player_state = "PLAYING"

    def register_handler(self, controller: Any) -> None:
        controller.registered(self.socket_client)

    def answer(self, data: dict) -> None:
        if self.outcome == "silent":
            return
        if self.outcome == "broadcast_error":
            self.handler_receive({"type": "playbackerror", "message": "NoCompatibleStream"})
            return
        # Like the real receiver: PlayNow loads a queue and plays its first item, NextTrack and
        # PreviousTrack move through it and do nothing at either end.
        if data["command"] == "PlayNow":
            self.queue, self.index = [item["Id"] for item in data["options"]["items"]], 0
        elif data["command"] == "NextTrack" and self.index + 1 < len(self.queue):
            self.index += 1
        elif data["command"] == "PreviousTrack" and self.index > 0:
            self.index -= 1
        else:
            return
        status = MediaStatus()
        playing = self.queue[self.index]
        status.content_id = f"http://jf.local:8096/videos/{playing}/master.m3u8?PlaySessionId=ps1&ApiKey={TOKEN}"
        self.loads += 1
        status.media_session_id = self.loads  # every load is a new media session
        if self.outcome == "play":
            status.player_state = "PLAYING"
            status.media_custom_data = {"playMethod": "Transcode", "playSessionId": "ps1"}
        else:  # error
            status.player_state = "IDLE"
            status.idle_reason = "ERROR"
        self.media_controller.status = status
        for listener in self.listeners:
            listener.new_media_status(status)

    def handler_receive(self, data: dict) -> None:
        self.receiver.receive_message(None, data)


class FakeComponent:
    def __init__(self, entities: dict[str, Any]) -> None:
        self.entities = entities

    def get_entity(self, entity_id: str) -> Any:
        return self.entities.get(entity_id)


async def setup(hass: HomeAssistant, outcome: str = "play", connected: bool = True) -> FakeChromecast:
    cast = FakeChromecast(outcome)
    entities = {
        CAST: SimpleNamespace(platform=SimpleNamespace(platform_name="cast"), _chromecast=cast if connected else None),
        "media_player.some_speaker": SimpleNamespace(platform=SimpleNamespace(platform_name="music_assistant")),
    }
    hass.data.setdefault(DATA_INSTANCES, {})["media_player"] = FakeComponent(entities)
    hass.states.async_set(CAST, "idle", {"friendly_name": "Bedroom Chromecast"})
    hass.config.components.add("cast")  # the manifest dependency, without starting the real one
    assert await async_setup_component(hass, "jellyfin_cast", CONFIG)
    await hass.async_block_till_done()
    return cast


async def play(hass: HomeAssistant, **extra: Any) -> dict:
    data = {"entity_id": CAST, "item_id": DASHED, "item_type": "Episode", **extra}
    return await hass.services.async_call("jellyfin_cast", "play_item", data, blocking=True, return_response=True)


async def test_plays_and_answers(hass: HomeAssistant) -> None:
    cast = await setup(hass)
    answer = await play(hass, audio_stream_index=2)
    assert answer["ok"] is True, answer
    assert answer["play_method"] == "Transcode" and answer["play_session"] == "ps1"
    assert answer["receiver_name"] == "Bedroom Chromecast Jellyfin"
    assert TOKEN not in answer["content_id"] and "ApiKey=…" in answer["content_id"]
    assert cast.launched == ["F007D354"]
    namespace, message = cast.sent[0]
    assert namespace == "urn:x-cast:com.connectsdk"
    assert message["command"] == "PlayNow" and message["accessToken"] == TOKEN
    assert message["serverAddress"] == "http://jf.local:8096"
    assert message["receiverName"] == "Bedroom Chromecast Jellyfin"
    assert message["options"]["items"] == [{"Id": ITEM, "Type": "Episode", "MediaType": "Video", "IsFolder": False}]
    assert message["options"]["audioStreamIndex"] == 2
    assert "subtitleStreamIndex" not in message["options"] and "maxBitrate" not in message


async def test_second_play_reuses_the_controller(hass: HomeAssistant) -> None:
    cast = await setup(hass)
    assert (await play(hass))["ok"] is True
    cast.media_controller.status.player_state = "IDLE"  # the first stream ended
    cast.media_controller.status.idle_reason = "FINISHED"
    assert (await play(hass))["ok"] is True
    assert len(cast.listeners) == 1 and len(cast.sent) == 2


async def test_tv_error_is_reported(hass: HomeAssistant) -> None:
    await setup(hass, "error")
    answer = await play(hass)
    assert answer["ok"] is False and "could not play" in answer["error"]


async def test_receiver_broadcast_error_is_reported(hass: HomeAssistant) -> None:
    cast = await setup(hass, "broadcast_error")
    cast.receiver = None

    original = cast.register_handler

    def keep(controller: Any) -> None:
        cast.receiver = controller
        original(controller)

    cast.register_handler = keep
    answer = await play(hass)
    assert answer["ok"] is False and "NoCompatibleStream" in answer["error"]


async def test_silent_tv_times_out(hass: HomeAssistant) -> None:
    await setup(hass, "silent")
    answer = await play(hass, timeout=5)
    assert answer["ok"] is False and "Not playing after 5 s" in answer["error"]


async def test_not_connected(hass: HomeAssistant) -> None:
    await setup(hass, connected=False)
    answer = await play(hass)
    assert answer == {"entity_id": CAST, "item_id": ITEM, "ok": False, "error": f"{CAST} is not connected"}


async def test_rejects_non_cast_players(hass: HomeAssistant) -> None:
    await setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("jellyfin_cast", "play_item",
                                       {"entity_id": "media_player.some_speaker", "item_id": ITEM},
                                       blocking=True, return_response=True)


async def test_without_response(hass: HomeAssistant) -> None:
    await setup(hass)
    assert await hass.services.async_call("jellyfin_cast", "play_item",
                                          {"entity_id": CAST, "item_id": ITEM}, blocking=True) is None
