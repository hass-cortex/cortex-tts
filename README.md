# Cortex TTS for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/hass-cortex/cortex-tts)](https://github.com/hass-cortex/cortex-tts/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2026.3.0+-green.svg)](https://www.home-assistant.io/)
[![GitHub License](https://img.shields.io/github/license/hass-cortex/cortex-tts)](https://github.com/hass-cortex/cortex-tts/blob/main/LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hass-cortex/cortex-tts)

Home Assistant integration for the [Cortex TTS app][app-repo]: one TTS entity
per model downloaded on the server, so every voice the app offers can be
picked in an Assist pipeline or a `tts.speak` action. The models — what each
costs, how each clones, which to pick — are documented on the app's side in
[Models][models]. This page is the Home Assistant half: entities,
`tts.speak`, the per-model speaking mode and the diagnostic sensors.

## Requirements

- **Home Assistant 2026.3.0 or newer** — the release that serves an
  integration's own `brand/` icons, and that brings the Python 3.14 the code
  is written for.
- **A Cortex TTS app speaking API version 5.** Not a release number: the
  integration reads `api_version` from the app's `/health` and sets up only
  against that exact version, refusing anything else with "unsupported API".
  Update whichever side is behind.

## Installation

Install the app and download a model first; the [App Store page][app-docs]
has the steps. Then add this integration through HACS and restart Home
Assistant.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hass-cortex&repository=cortex-tts&category=integration)

On Home Assistant OS and Supervised the app announces itself, so a **Cortex
TTS discovered** card appears under **Settings → Devices & services**; click
**Configure** and confirm. Container and Core installs add it by hand: the
app's address **including the scheme** (`http://homeassistant.local:8771`) and
the API key from the app's Configuration tab, with the app's port published.

[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cortex_tts)

Each entry is named after its address — `Cortex TTS (local-cortex-tts:8771)`,
`Cortex TTS (192.168.10.36:8771)` — so one on the Home Assistant box and one
on a GPU machine are told apart at a glance.

## Entities

One **TTS entity** per downloaded model, named after the model —
`tts.hojo_tts_light_40m`, `tts.moss_tts_nano` — plus nine sensors describing
the reply it spoke most recently. `<model>` is the slug of the model name.

| Sensor                  | Entity id                            | Unit | What it says                                                                                                                                                          |
| ----------------------- | ------------------------------------ | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Text**                | `sensor.<model>_text`                | —    | What was said; the state is the first 255 characters, the `text` attribute the whole                                                                                  |
| **Time to first audio** | `sensor.<model>_time_to_first_audio` | ms   | Request in, first frame out. Attributes split it: `load_ms`, `writer_ms`, `render_ms`                                                                                 |
| **Render time**         | `sensor.<model>_render_time`         | ms   | What the model was busy for, as the app measured it                                                                                                                   |
| **Audio length**        | `sensor.<model>_audio_length`        | s    | How long the reply plays for                                                                                                                                          |
| **Real-time factor**    | `sensor.<model>_real_time_factor`    | —    | This reply's render time over its audio length, fixed cost included; below 1 outruns playback                                                                         |
| **Text length**         | `sensor.<model>_text_length`         | —    | Characters in the reply                                                                                                                                               |
| **Playback margin**     | `sensor.<model>_playback_margin`     | s    | The least audio the listener still held as the reply left the app; negative means it ran dry                                                                          |
| **Render batches**      | `sensor.<model>_render_batches`      | —    | How many requests the app rendered the reply in                                                                                                                       |
| **Delivery mode**       | `sensor.<model>_delivery_mode`       | —    | How the app spoke the reply: **As it was written** (`streaming`) or **Held until finished** (`buffered`). On automatic, whichever the measured real-time factor chose |

Text, Delivery mode, Time to first audio and Playback margin sit on the
device's main card; the rest are diagnostic. All describe the last reply only
and clear when a new one begins; a failed synthesis leaves them unknown
rather than zero. The margin is measured only on a streamed reply.

Downloading a model or uploading a reference recording in the app reaches the
entity list in seconds, with no reload.

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

`language` decides which voices are on offer and sets the defaults for
`convert_script` and `taiwan_readings`. The default is the first of `zh-TW`,
`zh`, `en-US`, `en` the model supports. OmniVoice is also told to read the
text as that language, whole (`zh-TW`, not `zh`); there is no separate option
for it.

`voice` is an id, not a display name, and belongs to the model behind the
entity you targeted; a voice from another model is rejected. Omit it for the
server's default. How each model names its voices is [Models][models]; the
action below lists them.

A message need not end in punctuation, and numbers, units and Traditional
Chinese are rewritten on the server before synthesis — [the text
pipeline][text] shows into what. `cache: false` re-synthesises the same text
on every call.

### Finding a voice id

Home Assistant publishes an engine's voice list only to the dashboard, so
nothing in YAML can reach it. This action can:

```yaml
action: cortex_tts.list_voices
data:
  entity_id: tts.moss_tts_nano # optional; omit for every voice
response_variable: result
```

Name the entity you are going to speak through and the answer is the voices
it accepts. Omit it and the answer covers every model on every configured
server. An entity whose server is not loaded, or whose model that server has
since dropped, is an error rather than an empty list.

The response is `{voices: [...], count}`. Each voice holds `{voice, name,
language, gender, source, model, model_name}`. `source` is `builtin` for a
voice shipped with the model, `designed` for one OmniVoice builds from
attributes, and `reference` for a recording you uploaded. `language` is the
voice's, not the text's — a cloned voice declares the one chosen at upload —
and a voice that declares none is offered for every language.

### Per-call options

| Option             | Default                | Meaning                                                                                                                                                                                                        |
| ------------------ | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `voice`            | server default         | Voice id — built-in, designed or cloned                                                                                                                                                                        |
| `preferred_format` | `mp3`                  | Container for a buffered reply: `mp3`, `wav`, `flac` or `ogg`. Home Assistant asks for `mp3` unless told otherwise, so a plain `tts.speak` is never transcoded. A streamed reply is always MP3                 |
| `audio_output`     | as above               | Read only when `preferred_format` is absent                                                                                                                                                                    |
| `normalize_text`   | app rule, else `true`  | Expand units, clock times and dates in the language's own words                                                                                                                                                |
| `convert_script`   | Chinese languages only | Convert Traditional glyphs to Simplified                                                                                                                                                                       |
| `expand_numbers`   | app rule, else model   | Also read a bare number — no unit, clock or date around it — as a quantity. Left out, on only for a model that cannot say a digit (Hojo)                                                                       |
| `taiwan_readings`  | `zh-TW` / `zh-Hant`    | Respell words Taiwan reads differently (垃圾 lè sè) with homophones; off for `zh-CN`; a bare `zh` counts when the text is Traditional                                                                          |

`normalize_text` and `convert_script` exist for a caller whose text is already
prepared: turning conversion off for ordinary Traditional Chinese makes the
voice unintelligible, and turning normalisation off leaves units, times and
dates unread. Any of the four left out is answered by the app's own rule per
model and language first, then by the pipeline ([the text pipeline][text]).

## Options

Settings are per model, not per integration: each model the server offers
gets its own subentry. Open the integration, then **Configure** on the
model's row. A model cannot be added here — it appears by being downloaded in
the app.

[![Open your Home Assistant instance and show this integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=cortex_tts)

| Setting           | Default     | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Speaking mode** | `Automatic` | **Automatic** decides per reply from the real-time factor the app has measured for this model and voice on its host: spoken as it is written while the model keeps ahead of its audio, held until rendered when it does not or while unmeasured. **Stream** insists on speaking as it is written, and stalls audibly on a host that cannot keep up. **Wait for the whole reply** never stalls; its wait before the first word is the longest |

A change applies to the next reply; nothing reloads. Automatic is the default
for every model because the app carries no figure of its own: it uses only
what your host has measured, and holds a reply until it has a measurement.
Pin a model to stream only after `sensor.<model>_playback_margin` has stayed
positive on your host. How the app paces a reply, what each outcome means
and what to do when the margin goes negative are in [Keeping up][streaming].

## Troubleshooting

- **No voices in the picker.** The model is not downloaded, or it is a
  cloning model with no reference recording uploaded yet. Both are fixed in
  the app; the entity list follows in seconds.
- **A media player refuses the stream.** A player that probes the file before
  playing — AirPlay targets do — can give up waiting for the first bytes of a
  long reply. Pin that model's **Speaking mode** to _Wait for the whole
  reply_. Assist pipelines and voice satellites take streams without trouble.
- **It stutters near the end of long replies.** The model is not keeping up
  on this host; pin its **Speaking mode** to _Wait for the whole reply_ or
  pick a faster model, then read [Keeping up][streaming].
- **Chinese is read as the wrong words, or the mainland way.** The language
  tag was not a `zh-*` one, so `convert_script` and `taiwan_readings` stayed
  off. Send `language: zh-TW`. Anything else about the text is the app's own
  troubleshooting on its [App Store page][app-docs].
- **A "re-enter the API key" prompt.** The key was rotated in the app. Paste
  the current one from the app's Configuration tab; on Home Assistant OS the
  Supervisor re-announces the app and the entry updates by itself.
- **"Could not reach the Cortex TTS server" at setup.** The entry retries by
  itself once the app is up. Check the app is running and that the address
  carries the scheme (`http://…`); from outside the Supervisor network the
  port must be published in the app's Network settings.
- **"This app version speaks a different API".** Update whichever of the app
  and the integration is older.

## Contributing

Issues and pull requests are welcome; [CONTRIBUTING.md](CONTRIBUTING.md) has
the checks to run. A problem with the voice itself, the text pipeline or the
models usually belongs to [the app][app-repo].

## License

MIT — see [LICENSE](LICENSE).

[app-repo]: https://github.com/hass-cortex/app-cortex-tts
[app-docs]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/DOCS.md
[models]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/docs/models.md
[text]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/docs/text-pipeline.md
[streaming]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/docs/delivery.md
