# Cortex TTS for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/hass-cortex/cortex-tts)](https://github.com/hass-cortex/cortex-tts/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2026.3.0+-green.svg)](https://www.home-assistant.io/)
[![GitHub License](https://img.shields.io/github/license/hass-cortex/cortex-tts)](https://github.com/hass-cortex/cortex-tts/blob/main/LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hass-cortex/cortex-tts)

Home Assistant integration for the [Cortex TTS app][app-repo] — on-device
text-to-speech over whichever of its models you download, on your own
hardware.

Adds one TTS entity per model downloaded on the server — a model with no
weights on disk would be a permanently-failing voice in the picker — so every
voice the server offers can be selected in any Assist pipeline or `tts.speak`
action.

The models themselves — what each costs, how each clones, which to pick — are
documented on the app's side: [Models][models]. This page is about the Home
Assistant half: entities, `tts.speak`, the per-model speaking mode and the
diagnostic sensors.

## Features

- **On-device** — synthesis runs on your own hardware through the
  [Cortex TTS app][app-repo]. No cloud, no per-character bill.
- **One entity per model** — each model downloaded on the server becomes its
  own TTS entity, with its voices in the pipeline picker: built in, cloned
  from a recording you uploaded, or designed from a fixed set of attributes.
- **A style instruction where the model reads one** — Qwen3-TTS takes a
  plain-language note beside the voice (`speak slowly, in a warm tone`),
  offered as a `tts.speak` option on that entity alone.
- **Streaming, per model, and off until you ask** — a model that renders
  faster than its audio plays can start on the opening sentences instead of the
  last one. Every model starts buffered; you turn streaming on after your own
  sensors have said the model keeps up on your host.
- **A service for voice ids** — `cortex_tts.list_voices` answers what
  `tts.speak` needs, which Home Assistant otherwise publishes only to the
  dashboard.
- **Live voice sync** — downloading a model or uploading a reference recording
  reaches the entity list in seconds, with no reload.
- **Discovered automatically** on Home Assistant OS and Supervised — the app
  announces itself through the Supervisor, so the address and API key arrive
  without being typed.
- **Diagnostics for what the listener waits** — sensors per model describing
  the most recent reply, including the playback margin that says whether the
  renderer beat the speaker or lost to it.

## Requirements

- **Home Assistant 2026.3.0 or newer.** 2026.3 is the release from which Home
  Assistant serves an integration's own `brand/` icons, which is where this
  one's live; it also brings the Python 3.14 the code is written for.
- **Cortex TTS app 0.1.0 or newer**, which speaks API version 1. The
  integration reads `api_version` from the app's `/health` and refuses to set
  up with "unsupported API" when the two sides disagree — update whichever is
  older. An app too old to report `style_instruction` simply offers no
  `instruct` option; nothing else changes.

## Setup

### 1. Install the app and download a model

The integration speaks through the [Cortex TTS app][app-repo]; install and start
that first, following its own [App Store page][app-docs].

[![Open this app in your Home Assistant instance.](https://my.home-assistant.io/badges/supervisor_addon.svg)](https://my.home-assistant.io/redirect/supervisor_addon/?addon=24127962_cortex_tts&repository_url=https%3A%2F%2Fgithub.com%2Fhass-cortex%2Frepository)

Then download at least one model in the app's UI. Setup succeeds with nothing
downloaded, but it creates no entities — a model with no weights cannot speak,
so it gets no voice in the picker. Models downloaded later appear by
themselves.

### 2. Add this integration through HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hass-cortex&repository=cortex-tts&category=integration)

Press **Add**, then restart Home Assistant.

### 3. Pair it with the running app

On Home Assistant OS or Supervised, a **Cortex TTS discovered** card appears by
itself; click **Configure** and confirm — the address and API key come from the
app, so there is nothing to type. The app is reached over the Supervisor's
internal network as `http://local-cortex-tts:8771`, which is why its port need
not be published.

[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cortex_tts)

Each entry is named after the address it points at — `Cortex TTS
(local-cortex-tts:8771)`, `Cortex TTS (192.168.10.36:8771)` — so two of them,
one on the Home Assistant box and one on a machine with a GPU, are told apart
at a glance.

Container and Core installs have no Supervisor to announce through, so add it
by hand: the app's address **including the scheme**
(`http://homeassistant.local:8771`) and the API key from the app's
Configuration tab. The port is not published by default; for an address
reachable from outside the Supervisor network, publish 8771 under the app's
**Network** settings first.

### 4. Pick the voice in a pipeline

[![Open your Home Assistant instance and manage your voice assistants.](https://my.home-assistant.io/badges/voice_assistants.svg)](https://my.home-assistant.io/redirect/voice_assistants/)

On most models the voice is what picks the language — a Chinese voice is the
only thing that makes them read Chinese. Qwen3-TTS and OmniVoice take a
language of their own, and on those the pipeline's language is sent with every
reply, so the speaker is a timbre rather than a language.

## Entities

One **TTS entity** per downloaded model, named after the model —
`tts.hojo_tts_light_40m`, `tts.moss_tts_nano`, one per Qwen3-TTS checkpoint,
one for OmniVoice — plus eight diagnostic sensors describing the reply it
spoke most recently:

| Sensor                  | Entity id                             | Unit | What it says                                                                                                          |
| ----------------------- | ------------------------------------- | ---- | --------------------------------------------------------------------------------------------------------------------- |
| **Time to first audio** | `sensor.<model>_time_to_first_audio`  | ms   | Request in, first frame out — the wait a listener feels                                                               |
| **Last synthesis time** | `sensor.<model>_last_synthesis_time`  | ms   | Buffered reply: what the model cost, as the server measured it. Streamed reply: wall-clock from request to last frame |
| **Last audio length**   | `sensor.<model>_last_audio_length`    | s    | How long the reply plays for                                                                                          |
| **Real-time factor**    | `sensor.<model>_real_time_factor`     | —    | Synthesis time over audio length; below 1 outruns playback                                                            |
| **Last text length**    | `sensor.<model>_last_text_length`     | —    | Characters in the reply                                                                                               |
| **Playback margin**     | `sensor.<model>_playback_margin`      | s    | The least audio the listener still held when a piece of the reply arrived; negative means it had run dry             |
| **Requests**            | `sensor.<model>_requests`             | —    | How many times the server was asked for this reply — one, whatever the mode, when there was nothing to group          |
| **Last synthesis mode** | `sensor.<model>_last_synthesis_mode`  | —    | `Buffered`, `Sentence by sentence` or `Sentences in groups` — the setting the reply began under, not the shape it came out in |

`<model>` is the slug of the model name, as in the TTS entity id. The margin
is measured only on a reply that arrived in more than one piece — a buffered
one, or a streamed one short enough to be handed over whole, leaves it
unknown, because nothing could arrive late.

They are diagnostic and describe the last reply only, never a running total, so
two replies are never mixed. All of them clear when a new reply begins: a
failed synthesis leaves them unknown rather than zero, because zero would read
as "instant".

A streamed reply settles its numbers at two moments — the wait and the mode are
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

`language` decides which voices are on offer and sets the default for
`convert_script`; number expansion stays on either way. The default is the
first of `zh-TW`, `zh`, `en-US`, `en` the model supports. On Qwen3-TTS and
OmniVoice it is also what the model is told to read the text as — whole, as
`zh-TW` rather than `zh`, because how much of a tag matters is the model's to
decide. There is no second option for it: this one already means "what
language is this", which is exactly what the model needs.

`voice` is an id, not a display name, and it belongs to one model — the one
behind the entity you targeted. A voice from another model is rejected. Omit it
for the server's default. Every model names its voices differently
(`hojo_zh_f_01`, `anna-su`, `Yuewen`); [Models][models] says how, and the
action below lists them.

A message need not end in punctuation, and numbers, units and Traditional
Chinese are rewritten on the server before synthesis — the example above
reaches the model as Simplified glyphs with both figures written out; [the
text pipeline][text] shows exactly into what.

Adding `cache: false` to `tts.speak` re-synthesises the same text on every
call; it is worth it only when the message is different every time.

### Finding a voice id

Home Assistant publishes an engine's voice list to the dashboard, over a
WebSocket command with no action equivalent, so nothing in YAML can reach it.
This integration adds an action that can:

```yaml
action: cortex_tts.list_voices
data:
  entity_id: tts.moss_tts_nano   # optional; omit for every voice
response_variable: result
```

Pass the entity you are going to speak through and the answer is the voices
that entity accepts — the model's, on its own server. Omit it and the answer
covers every model on every configured server, which is the question to ask
when you are choosing an entity rather than a voice.

Naming an entity whose server is not loaded, or whose model that server has
since dropped, is an error rather than an empty list: an empty list would read
as "this model has no voices", which is a different problem.

The response is `{voices: [...], count}`. Each voice holds `{voice, name,
language, gender, source, model, model_name}`. `source` is one of three:
`builtin` for a voice shipped with the model, `designed` for one OmniVoice
builds from a fixed set of attributes, and `reference` for a recording you
uploaded. `language` is the language of the voice, not of the text — a cloned
voice declares whichever you chose when uploading the recording — and that is
what a pipeline filters on. A voice that declares none, as a designed one
does, is offered for every language.

It is also how a conversation agent picks a voice by description instead of by
an id nobody remembers.

### Per-call options

| Option             | Default                | Meaning                                                                                                                                                                                                                                                                       |
| ------------------ | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `voice`            | server default         | Voice id — built-in or cloned. See above for how to find one                                                                                                                                                                                                                  |
| `preferred_format` | `mp3`                  | Container for a buffered reply: `mp3`, `wav`, `flac` or `ogg`, answered directly. Home Assistant asks for `mp3` unless told otherwise, so a plain `tts.speak` is never transcoded. A streamed reply is always MP3, which is what a stream can be without declaring a length it does not know |
| `audio_output`     | as above               | Read only when `preferred_format` is absent                                                                                                                                                                                                                                   |
| `normalize_text`   | `true`                 | Expand numbers, units, dates and clock times, in the script of the text                                                                                                                                                                                                       |
| `convert_script`   | Chinese languages only | Convert Traditional glyphs to Simplified                                                                                                                                                                                                                                      |
| `instruct`         | none                   | A plain-language instruction beside the voice — `speak slowly, in a warm tone`. Offered **only on Qwen3-TTS 0.6B (built-in voices)**, the one model that reads one; on any other entity Home Assistant refuses the option before the request is sent |

The two text switches exist for a caller whose text is already prepared;
turning conversion off for ordinary Traditional Chinese makes the voice
unintelligible, and turning normalisation off leaves every digit silent.

## Options

Settings are per model, not per integration: each model the server offers gets
its own subentry, because the right answer differs between a model that renders
in a quarter of real time and one that does not.

[![Open your Home Assistant instance and show this integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=cortex_tts)

Open the integration, then **Configure** on the model's row. A model cannot be
added here — it appears by being downloaded in the app.

| Setting           | Default    | Meaning                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Speaking mode** | `Buffered` | `Buffered` renders the whole reply, then plays it — the longest wait, and it never stalls. `Sentence by sentence` speaks each sentence as it is finished: the quickest first word, with a join between every sentence. `Sentences in groups` sends together whatever was written while the last request was still rendering, so there are fewer joins — on a model that emits audio while a request is still rendering that removes the pauses outright, and on one that returns each request whole it trades several short waits for fewer longer ones.                         |
| **Head start**    | `0` s      | Seconds of audio to bank before a streamed reply begins playing, up to 10. A model that renders slower than its audio plays falls behind for the whole reply and never catches up; this hands it the difference in advance. Paid on time to first audio, and a grouped reply whose full length is known before the first request is charged only what a reply that long can lose. It banks by the audio it has received, so it is only a lever on a model that emits audio while a request is still rendering — the Hojo models return each request whole, and a bank smaller than the first request is already full when it arrives. |

A change applies to the next reply; nothing reloads.

**Buffered is the default for every model, deliberately.** There is no
figure to decide for you: a real-time factor belongs to a host, so the app
carries none and shows only what your own machine has measured. Use the
model for a while, read `sensor.<model>_real_time_factor`, and switch to
sentences in groups if it sits comfortably under **0.5**; then watch
`sensor.<model>_playback_margin`. The reasoning, the measurements and what to
do when the margin goes negative are in [Keeping up][streaming].

## How it works

### Streaming

A long reply is normally silent until the last word has been synthesised. In
either streaming mode, each finished sentence is synthesised and played while
the rest is still being written, so the wait before the first word stops
growing with the length of the reply.

Sentence boundaries come from
[`sentence-stream`](https://github.com/OHF-Voice/sentence-stream), the same
splitter Home Assistant's own streaming engines use; it handles `。！？` as well
as Latin punctuation.

A model has to synthesise faster than its audio plays to keep a stream fed; a
cloning model also has to hold one voice steady across separately synthesised
sentences, so leave a cloning model buffered unless you have listened to it
streamed. Chunk streaming is not a cure for a slow model: Qwen3-TTS emits
audio mid-sentence and is still the slowest model here, so on a host where it
renders at several times real time every block arrives later than the last.
Set a model back to buffered if a media player refuses the stream: a player
that probes the file before playing — AirPlay targets do — can give up waiting
for the first bytes of a long reply and fail to open it, while the audio itself
is perfectly fine. Assist pipelines and voice satellites take streams without
trouble.

Each model's **Last synthesis mode** sensor reports the mode the model was set
to when the reply began. Home Assistant routes every reply through that mode —
a whole message handed to `tts.speak` included, which it wraps as a one-item
stream — so a model set to sentences in groups speaks announcements that way too.

### Talking to the app

- A refusal from the server is reported as the server's own error code and
  message, so the Home Assistant error names the actual cause.
- A rejected key — rotated in the app after setup — starts a re-authentication
  flow by itself; the integration asks for the new key rather than failing every
  reply the same way. On Home Assistant OS the Supervisor re-announces the app
  with the new key, and that updates the entry with nothing to type.
- The limits are on silence from the server rather than on the whole exchange,
  because synthesis is CPU-bound and grows with the text: 10 s to connect, then
  180 s between bytes for a buffered reply and 60 s for a streamed one. Other
  requests to the app time out at 10 s.

## Troubleshooting

- **No voices in the picker.** The model is not downloaded — the entity only
  exists once it is — or it is a cloning model with no reference recording
  uploaded yet. Both are fixed in the app; the entity list follows in seconds.
- **The right words, mispronounced or garbled, in Chinese.** `convert_script`
  was turned off for Traditional Chinese text, or the language tag was not a
  `zh-*` one so conversion never ran. Send `language: zh-TW`.
- **Digits are silent.** `normalize_text` was turned off. The model pronounces
  no Arabic numeral at all.
- **It stutters near the end of long replies.** The model is not keeping up on
  this host; set its **Speaking mode** back to buffered, then read
  [Keeping up][streaming].
- **A "re-enter the API key" prompt.** The key was rotated in the app. Paste the
  current one from the app's Configuration tab.
- **"Could not reach the Cortex TTS server" at setup.** The entry retries by
  itself once the app is up. Check the app is running and that the address
  carries the scheme (`http://…`); from outside the Supervisor network the port
  must be published in the app's Network settings.
- **"This app version speaks a different API".** Update whichever of the app
  and the integration is older.

## Contributing

Issues and pull requests are welcome; [CONTRIBUTING.md](CONTRIBUTING.md) has
the checks to run. The app this speaks through lives in
[hass-cortex/app-cortex-tts][app-repo]; a problem with the voice itself, the
text pipeline or the models usually belongs there.

## License

MIT — see [LICENSE](LICENSE).

[app-repo]: https://github.com/hass-cortex/app-cortex-tts
[app-docs]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/DOCS.md
[models]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/docs/models.md
[text]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/docs/text-pipeline.md
[streaming]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/docs/streaming.md
