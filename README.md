# Cortex TTS for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/hass-cortex/cortex-tts)](https://github.com/hass-cortex/cortex-tts/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2026.3.0+-green.svg)](https://www.home-assistant.io/)
[![GitHub License](https://img.shields.io/github/license/hass-cortex/cortex-tts)](https://github.com/hass-cortex/cortex-tts/blob/main/LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hass-cortex/cortex-tts)

Home Assistant integration for the [Cortex TTS app](https://github.com/hass-cortex/app-cortex-tts) —
on-device, CPU-only text-to-speech.

Adds one TTS entity per model downloaded on the server — a model with no
weights on disk would be a permanently-failing voice in the picker — so every
voice the server offers can be selected in any Assist pipeline or `tts.speak`
action.

## Features

- **On-device** — synthesis runs on your own hardware through the
  [Cortex TTS app][app-repo]. No cloud, no API key, no per-character bill.
- **One entity per model** — each model downloaded on the server becomes its
  own TTS entity, with its built-in or cloned voices in the pipeline picker.
- **Per-sentence playback** — a long reply starts speaking after its first
  sentence instead of after its last.
- **The text pipeline the model lacks** — Traditional-to-Simplified conversion
  and number, unit, date and clock expansion, applied server-side before
  synthesis.
- **Live voice sync** — downloading a model or uploading a reference recording
  reaches the entity list in seconds, with no reload.
- **Discovered automatically** — the app announces itself through the
  Supervisor, so the address and API key arrive without being typed.
- **Diagnostics for what the listener waits** — six sensors per model, all
  describing the most recent reply.

## Setup

### 1. Install the app

The integration speaks through the [Cortex TTS app][app-repo]; install and start
that first.

[![Open this app in your Home Assistant instance.](https://my.home-assistant.io/badges/supervisor_addon.svg)](https://my.home-assistant.io/redirect/supervisor_addon/?addon=24127962_cortex_tts&repository_url=https%3A%2F%2Fgithub.com%2Fhass-cortex%2Frepository)

### 2. Add this integration through HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hass-cortex&repository=cortex-tts&category=integration)

Press **Add**, then restart Home Assistant.

### 3. Pair it with the running app

A **Cortex TTS discovered** card appears by itself; click **Configure** and
confirm — the address and API key come from the app, so there is nothing to
type.

[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cortex_tts)

To add it by hand instead, use the app's address (`http://homeassistant.local:8771`)
and the API key from the app's Configuration tab.

### 4. Pick the voice in a pipeline

[![Open your Home Assistant instance and manage your voice assistants.](https://my.home-assistant.io/badges/voice_assistants.svg)](https://my.home-assistant.io/redirect/voice_assistants/)

The voice is what picks the language: the model takes no language parameter,
so a Chinese voice is the only thing that makes it read Chinese.

## Entities

One **TTS entity** per downloaded model, plus six diagnostic sensors describing
the reply it spoke most recently:

| Sensor                  | Unit | What it says                                               |
| ----------------------- | ---- | ---------------------------------------------------------- |
| **Time to first audio** | ms   | Request in, first frame out — the wait a listener feels    |
| **Last synthesis time** | ms   | What the model cost, summed across sentences               |
| **Last audio length**   | s    | How long the reply plays for                               |
| **Real-time factor**    | —    | Synthesis time over audio length; below 1 outruns playback |
| **Last text length**    | —    | Characters in the reply                                    |
| **Last synthesis mode** | —    | `Streamed` or `Buffered`                                   |

They are diagnostic and describe the last reply only, never a running total, so
two replies are never mixed. All of them clear when a new reply begins: a
failed synthesis leaves them unknown rather than zero, because zero would read
as "instant".

Sentence-by-sentence replies settle their numbers at two moments — the wait is
known when the first frame leaves, the totals only once the last sentence is
rendered — so each lands as soon as it becomes true.

## Speaking

The entity is the target; everything the engine itself understands goes in
`options`.

```yaml
action: tts.speak
target:
  entity_id: tts.hojo_tts_light_40m
data:
  media_player_entity_id: media_player.living_room_speaker
  language: zh-TW
  options:
    voice: hojo_zh_f_02
  message: 洗衣機洗好了，目前室內溫度 26.5°C。
```

`language` takes `zh`, `zh-TW`, `zh-CN`, `zh-HK`, `zh-Hant`, `zh-Hans`, `en`,
`en-US`, `en-GB` or `en-AU`. The default is the first of `zh-TW`, `zh`,
`en-US`, `en` the model actually supports, so both shipped models default to
`zh-TW`. It decides which voices are on offer, and it sets the default for
`convert_script` — number expansion stays on either way.

`voice` is an id rather than a display name, and it has to belong to the model
behind the entity you targeted: the 40M entity takes the built-in ids
(`hojo_zh_f_01`, `hojo_zh_f_02`, and the `hojo_en_*` set), the cloning entity
takes the ids of the recordings you uploaded, which the app's **Cloned voices**
page lists. A voice from the wrong model is rejected. Omit it for the server's
default.

Adding `cache: false` re-synthesises the same text on every call; it is worth
it only when the message is different every time.

## Options

[![Open your Home Assistant instance and show this integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=cortex_tts)

Open the integration and click **Configure**:

| Option          | Default            | Meaning                                           |
| --------------- | ------------------ | ------------------------------------------------- |
| `stream_models` | models fast enough | Which models speak each sentence as it is written |

Per-call options on `tts.speak`:

| Option             | Default                | Meaning                                                                                |
| ------------------ | ---------------------- | -------------------------------------------------------------------------------------- |
| `voice`            | server default         | Voice id — a built-in voice or a cloned one                                            |
| `preferred_format` | `wav`                  | `wav`, `flac` or `ogg`; a streamed reply is always WAV and converted by Home Assistant |
| `audio_output`     | `wav`                  | The same three values, read only when `preferred_format` is absent                     |
| `normalize_text`   | `true`                 | Expand numbers, units, dates and clock times, in the script of the text                |
| `convert_script`   | Chinese languages only | Convert Traditional glyphs to Simplified                                               |

[app-repo]: https://github.com/hass-cortex/app-cortex-tts

## How it works

### Traditional Chinese and numbers

The underlying model cannot pronounce Traditional Chinese glyphs — measured at
32% character error rate against 4% once converted to Simplified. It also has
no text normalisation, so `26.5°C` and `14:35` come out as noise.

Both are fixed on the server before synthesis. Number expansion runs for
every language — the model pronounces no Arabic numeral at all, so an
unexpanded digit is silent rather than merely wrong — while glyph conversion
follows the language tag, because rewriting into Simplified is meaningless
outside Chinese. The `normalize_text` and `convert_script` options exist for
callers whose text is already prepared; turning conversion off for ordinary
Traditional Chinese will make the voice unintelligible.

### Per-sentence synthesis

A long reply is normally silent until the last word has been synthesised. With
this on, each finished sentence is synthesised and played while the rest is
still being written.

Sentence boundaries come from
[`sentence-stream`](https://github.com/OHF-Voice/sentence-stream), the same
splitter Home Assistant's own streaming engines use; it handles `。！？` as well
as Latin punctuation.

The wait before the first word stops growing with the length of the reply — it
becomes the time to synthesise one sentence, whatever follows it.

It is chosen per model, under **Configure**, and only the 40M is ticked to start
with. A model has to synthesise faster than its audio plays to keep a stream
fed; a cloning model also has to hold one voice steady across separately
synthesised sentences, which is untested, so the 80M is left for a deliberate
choice. Untick a model if a media player refuses the stream: a player that
probes the file before playing — AirPlay targets do — can give up waiting for
the first bytes of a long reply and fail to open it, while the audio itself is
perfectly fine. Assist pipelines and voice satellites take streams without
trouble.

Each model's **Last synthesis mode** sensor reports what actually happened —
`Streamed` or `Buffered`. That is not the same as the tick: Home Assistant
decides per request, so a ticked model still speaks the buffered way when the
caller handed it the whole message at once.

## Contributing

Issues and pull requests are welcome. The app this speaks through lives in
[hass-cortex/app-cortex-tts][app-repo]; a problem with the voice itself, the text
pipeline or the models usually belongs there.

## License

MIT — see [LICENSE](LICENSE).

[app-repo]: https://github.com/hass-cortex/app-cortex-tts
