<p align="center">
  <a href="#how-it-works"><img src="https://img.shields.io/badge/How_it_works-the_official_receiver-6f42c1" alt="The sound and the picture finally agree"></a>
  <a href="#install"><img src="https://img.shields.io/badge/Install-HACS_or_copy%2Fpaste-2da44e" alt="One folder, I promise"></a>
  <a href="#the-token"><img src="https://img.shields.io/badge/Token-not_an_API_key-d29922" alt="Ask me how I know"></a>
  <a href="#playing-by-name"><img src="https://img.shields.io/badge/Play-by_name-0e8a16" alt="Just say Futurama"></a>
  <a href="#shuffling-a-show"><img src="https://img.shields.io/badge/Shuffle-until_you_stop_it-8250df" alt="200 episodes. Good luck finishing that"></a>
  <a href="#good-to-know"><img src="https://img.shields.io/badge/Good_to_know-the_fine_print-cf222e" alt="Read this before yelling at me"></a>
</p>

# Jellyfin Cast for Home Assistant

My TVs are all Chromecasts of some sort, and my shows all live on Jellyfin.

Casting them from Home Assistant meant throwing a stream URL at Google's Default Media Receiver. It works... until the sound slowly drifts away from the picture halfway through an episode. Not great when the punchline lands two seconds late.

The official Jellyfin receiver (the app your TV opens when you hit the cast button in Jellyfin) plays way better. It picks the stream itself, and plays the file as it is whenever the TV can take it. The catch: Home Assistant can only *launch* it. Then the receiver just sits there, waiting for someone to tell it what to play.

This integration is that someone.

> [!NOTE]
> It's what plays shows and movies on my TVs since 2026-11-09 (Home Assistant 2026.11, Jellyfin 12.4). Playing by name and `library` came the day after, and they work in my testing on live Chromecasts. `shuffle` and the filtered TV picker are the newest: they pass the test suite (a real Home Assistant core with a fake Chromecast) and were checked against my own Jellyfin server, but haven't met a real TV yet.
> ᵃⁿᵈ ʷᵉ ⁿᵉᵉᵈ ᵇᵉᵗᵃ ᵗᵉˢᵗᵉʳˢ

---

## How it works

1. Home Assistant already has a connection to the TV (the Google Cast integration). Like DashCast, this integration borrows it, so nothing else connects to your TV.
2. It launches the Jellyfin receiver if something else is on screen.
3. It sends the receiver the same `PlayNow` message the Jellyfin apps send: your server, a token and the item.
4. The receiver asks Jellyfin for a stream that fits the TV, and reports playback to Jellyfin like any other client.
5. The action waits until the TV actually plays it, and tells you how it went. If it didn't, your script can fall back to something else.

## Install

**HACS:** add `https://github.com/maxi1134/jellyfin_cast` as a custom repository (type *Integration*), install *Jellyfin Cast*, restart.

**By hand:** copy `custom_components/jellyfin_cast` into your `config/custom_components/`, restart.

You need the Google Cast integration set up (your TVs are its `media_player` entities). Setup is YAML only:

```yaml
# configuration.yaml
jellyfin_cast:
  url: http://jellyfin.local:8096        # your Jellyfin server, as the TVs can reach it
  user_id: <Jellyfin id of the token's user>
  token: !secret jellyfin_cast_token
  # app_id: F007D354                     # optional, the Stable Jellyfin receiver
```

### The token

It has to be a **user access token**. An API key won't do: the receiver asks the server for `/Users/Me`, and an API key has no "me".

What I did, and what I'd recommend:
- Make a Jellyfin user just for Home Assistant (hidden from the login screen), with access to the libraries it should play. Bonus: its watch history stays out of your own *Continue Watching*.
- Get its token by turning Quick Connect on for a minute, signing that user in with the code, then turning Quick Connect off again. Signing in through the API (`/Users/AuthenticateByName`) works too.
- In that user's playback settings, turn **off** "Play next episode automatically". Otherwise playing one episode queues the rest of the season.
- Set its subtitle and audio languages. The receiver follows that user's settings.

`user_id` is in the address bar when you open that user in the Jellyfin dashboard.

## Play something

```yaml
action: jellyfin_cast.play_item
data:
  entity_id: media_player.living_room_tv
  name: Futurama
response_variable: cast
```

| Field | Required | What it does |
|---|---|---|
| `entity_id` | yes | The TV: a Google Cast `media_player` (the cast entity itself, not a group or universal one) |
| `name` | one of these two | A movie, show, episode or video, by name (see [*Playing by name*](#playing-by-name)) |
| `item_id` | one of these two | A Jellyfin item id, with or without dashes (see [*Finding an item_id*](#finding-an-item_id)) |
| `item_type` | no | With a name: only look for `Movie`, `Series` (shows), `Episode` or `Video`. With an item_id: its type (`Video` by default) |
| `library` | no | With a name: only look in that library, like `Movies` or `TV Shows` |
| `episode` | no | For a show: `next_up` (default) or `random` |
| `shuffle` | no | For a show: random episodes until you stop it (see [*Shuffling a show*](#shuffling-a-show)) |
| `audio_stream_index` | no | Jellyfin stream index of the audio track |
| `subtitle_stream_index` | no | Jellyfin stream index of the subtitles, `-1` for none |
| `start_position_ticks` | no | Where to start (10,000,000 ticks per second) |
| `max_bitrate` | no | Bitrate cap in bit/s (by default the receiver measures your network) |
| `receiver_name` | no | Name of the Jellyfin session (the TV's name + " Jellyfin" by default). Keep it the same per TV |
| `timeout` | no | Seconds to wait for the TV to play (45 by default) |

The response says `ok: true` with what it found and how it plays (`play_method`: DirectPlay, DirectStream or Transcode; `play_session`; the stream URL with the token masked), or `ok: false` with an `error`.

## Playing by name

It searches your server as the token's user, so it only finds what that user can see.

- Case, accents and punctuation don't matter: `how its made` finds *How It's Made*.
- An exact name wins. If there isn't one, it takes a title that contains your words: `matrix reloaded` finds *The Matrix Reloaded*. Episodes don't count for those looser matches unless you ask for `item_type: Episode`. Their titles are everywhere, and "Lava" shouldn't start a cartoon episode when you wanted the lava lamp video.
- A **show** plays one of its episodes: **Next Up** for the token's user (the next unwatched one, or the first episode once you've seen it all), or a random one with `episode: random`. The receiver reports playback under that same user, so Next Up keeps up with you.
- Same name twice (*Fargo* the movie and *Fargo* the show)? A movie or a show beats a video, which beats an episode, then the server's order decides. The others come back in `other_matches`, so you can pick with `item_type`, `library` or `item_id`.
- `library` keeps the search inside one of your libraries: `Fargo` in `TV Shows` plays the show, in `Movies` the film.

```yaml
ok: true
query: Futurama
item_id: 0d3f…              # what's playing
name: Space Pilot 3000
type: Episode
series: Futurama
season: 1
episode: 1
series_id: 5e1e…
episode_choice: next_up
matched: exact              # or partial
# other_matches: [...]      when the name wasn't unique
# library: TV Shows         when you gave one
```

Nothing found? `ok: false`, and `candidates` lists what the server suggested instead. Handy when a voice assistant has to answer something.

## Shuffling a show

```yaml
action: jellyfin_cast.play_item
data:
  entity_id: media_player.living_room_tv
  name: Futurama
  shuffle: true
```

Every episode in random order, then all of them again in a new order, and so on, until you stop it or play something else. It goes to the TV as one queue of 200 episodes: no repeats before everything has played once, never the same episode twice in a row. That's about 70 hours of a 22-minute show, so in practice it runs until you stop it.

The queue lives on the TV. Home Assistant sends it once and forgets about it, and skipping on the TV just moves through it. The receiver has its own Shuffle too, but it queues 50 episodes and stops, which on a short show means you're done after one pass.

`shuffle` works with a show's own `item_id` too. It replaces `episode`, and anything that isn't a show just plays normally.

## Finding an item_id

You only need one to pin an exact item, like a dashboard button that always starts the same movie, or one of two shows with the same name.

- **In the Jellyfin web app:** open the movie or episode. The id is in the address bar: `…/web/#/details?id=1ffa07c043d35bab9a1c090831a41d13&serverId=…`. Shows and seasons have ids too, but you can't play those directly. Use an episode's id, or play the show by name or with `shuffle`.
- **From a name lookup:** play it once by name and keep the `item_id` (or `series_id`, or one from `other_matches`) from the response.

## The TV picker

In the action editor, the TV list only shows cast players that can actually show video: Chromecasts, Google TVs, TVs with Chromecast built in, Nest Hubs. Speakers, speaker groups and cast-enabled audio receivers stay out (the Google Cast integration marks those as `speaker`). Give one of them another device class if you really want it in there. Scripts and automations can still target any cast player.

## Good to know

- **Old devices:** my 1st-gen Nest Hub won't play what the receiver picks for a 4K movie (720p worked fine). I send the Hub to the Default Media Receiver instead, and use the `ok: false` answer as a fallback for everything else.
- **Restarting Home Assistant stops what it cast.** Home Assistant is the only sender, and the receiver quits when its last sender leaves. Don't restart in the middle of movie night.
- Each TV shows up in Jellyfin as a *Chromecast* session called "<TV name> Jellyfin". If you have the Jellyfin integration, it'll give you a media player for it.
- `receiver.py` only needs `pychromecast`, so you can poke at a real TV outside Home Assistant.

## Tests

```
pip install -r requirements_test.txt
pytest
```

39 tests, running a real Home Assistant core with a fake Chromecast and a mocked Jellyfin API.

---

It's part of [my smart home](https://github.com/maxi1134/Home-Assistant-Config): the TV-show buttons on my wall tablets and my voice assistant ("play Futurama in the salon") go through it. If you want to see it used for real, check `jellyfin_play_item_on_a_tv` in my [`scripts.yaml`](https://github.com/maxi1134/Home-Assistant-Config/blob/master/scripts.yaml), with the fallback and all.
