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
- **Streaming, per model, and off until you ask** — set a model to
  automatic and the app speaks each reply as it is written, pacing it from
  what it has measured about that model on its own host. Every model starts
  buffered; you switch it after your own sensors have a figure.
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
- **A Cortex TTS app speaking API version 4.** Not a release number: the
  integration reads `api_version` from the app's `/health` and sets up only
  against that exact version, refusing anything else — older or newer — with
  "unsupported API". The app's own page reports what it speaks; update
  whichever side is behind.

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

The pipeline's language is sent with every reply and decides how the text is
prepared. On most models the voice is what picks the language the model
speaks — a Chinese voice is the only thing that makes them read Chinese.
Qwen3-TTS and OmniVoice take the language themselves, so there the speaker is
a timbre rather than a language.

## Entities

One **TTS entity** per downloaded model, named after the model —
`tts.hojo_tts_light_40m`, `tts.moss_tts_nano`, one per Qwen3-TTS checkpoint,
one for OmniVoice — plus nine sensors describing the reply it
spoke most recently:

| Sensor                  | Entity id                            | Unit | What it says                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------- | ------------------------------------ | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Text**                | `sensor.<model>_text`                | —    | What was said, as handed to the app; the state is the first 255 characters, the `text` attribute the whole                                                                                                                                                                                                                                                                                                               |
| **Time to first audio** | `sensor.<model>_time_to_first_audio` | ms   | Request in, first frame out — the wait a listener feels. Three attributes split it: `load_ms` where the model had to be made resident, `writer_ms` for the agent's own writing, `render_ms` for the synthesis                                                                                                                                                                                                            |
| **Render time**         | `sensor.<model>_render_time`         | ms   | What the model was busy for, as the app measured it — never this side's clock, which also spans the writer and the opening hold                                                                                                                                                                                                                                                                                          |
| **Audio length**        | `sensor.<model>_audio_length`        | s    | How long the reply plays for                                                                                                                                                                                                                                                                                                                                                                                             |
| **Real-time factor**    | `sensor.<model>_real_time_factor`    | —    | This reply's render time over its audio length; below 1 outruns playback. One reply, its per-request fixed cost included — the app's own card shows the fitted slope with that cost held apart, so it reads at or under this                                                                                                                                                                                             |
| **Text length**         | `sensor.<model>_text_length`         | —    | Characters in the reply                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Playback margin**     | `sensor.<model>_playback_margin`     | s    | The least audio the listener still held as the reply left the app; negative means it had run dry                                                                                                                                                                                                                                                                                                                         |
| **Render batches**      | `sensor.<model>_render_batches`      | —    | How many requests the app rendered the reply in: one for a whole reply, several for a live one                                                                                                                                                                                                                                                                                                                           |
| **Delivery mode**       | `sensor.<model>_delivery_mode`       | —    | How the app is speaking the reply, then how it spoke it. In flight it is **As it was written** (`streaming`) or **Planned in advance** (`planned`); when the reply ends, one that fitted a single request is replaced by **One request** (`whole`). **Held until finished** (`buffered`) says the reply was held to the end whatever the request count, which is what a model set to **Speaking mode: buffered** reports |

Text, Delivery mode, Time to first audio and Playback margin sit on the
device's main card — what was said and how it went. The rest measure the model
and are diagnostic.

`<model>` is the slug of the model name, as in the TTS entity id. The margin
is measured only on a reply the app spoke live; a buffered one leaves it
unknown, because nothing could arrive late.

They are diagnostic and describe the last reply only, never a running total, so
two replies are never mixed. All of them clear when a new reply begins: a
failed synthesis leaves them unknown rather than zero, because zero would read
as "instant".

A live reply settles its numbers at three moments — the text when the writer
finishes, the wait and the delivery mode when the first frame arrives, the
totals when the app reports how it went — so each lands as soon as it becomes
true.

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
`convert_script` and `taiwan_readings`; number expansion stays on either way. The default is the
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
  entity_id: tts.moss_tts_nano # optional; omit for every voice
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

| Option             | Default                | Meaning                                                                                                                                                                                                                                                                                      |
| ------------------ | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `voice`            | server default         | Voice id — built-in or cloned. See above for how to find one                                                                                                                                                                                                                                 |
| `preferred_format` | `mp3`                  | Container for a buffered reply: `mp3`, `wav`, `flac` or `ogg`, answered directly. Home Assistant asks for `mp3` unless told otherwise, so a plain `tts.speak` is never transcoded. A streamed reply is always MP3, which is what a stream can be without declaring a length it does not know |
| `audio_output`     | as above               | Read only when `preferred_format` is absent                                                                                                                                                                                                                                                  |
| `normalize_text`   | app rule, else `true`  | Expand units, clock times and dates in the language's own words                                                                                                                                                                                                                              |
| `convert_script`   | Chinese languages only | Convert Traditional glyphs to Simplified                                                                                                                                                                                                                                                     |
| `expand_numbers`   | app rule, else model   | Also read a bare number — one with no unit, clock or date around it — as a quantity. Left out, on only for a model that cannot say a digit at all (Hojo); off otherwise, because such a number is as often a room, a phone number or a model as a count, and a wrong reading misleads        |
| `taiwan_readings`  | `zh-TW` / `zh-Hant`    | Respell words Taiwan reads differently (垃圾 lè sè) with homophones the model reads that way; off for `zh-CN`; a bare `zh` counts when the text is Traditional                                                                                                                               |
| `instruct`         | none                   | A plain-language instruction beside the voice — `speak slowly, in a warm tone`. Offered **only on Qwen3-TTS 0.6B (built-in voices)**, the one model that reads one; on any other entity Home Assistant refuses the option before the request is sent                                         |

`normalize_text` and `convert_script` exist for a caller whose text is already prepared;
turning conversion off for ordinary Traditional Chinese makes the voice
unintelligible, and turning normalisation off leaves units, times and dates
unread — on a Hojo model it leaves bare digits silent as well. Any of
the four left out is answered by the app's own settings first — a rule per
model and language on its Settings page — and only then by the pipeline.

## Options

Settings are per model, not per integration: each model the server offers gets
its own subentry, because the right answer differs between a model that renders
in a quarter of real time and one that does not.

[![Open your Home Assistant instance and show this integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=cortex_tts)

Open the integration, then **Configure** on the model's row. A model cannot be
added here — it appears by being downloaded in the app.

| Setting           | Default                    | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Speaking mode** | `Wait for the whole reply` | Four answers, and the last three take the decision away from the app. **Automatic** pieces the reply out as it is written, from what it has measured about this model on its host: streamed while the model keeps ahead of its audio, planned when it does not, and planned from its own first request while it is unmeasured. **Plan the whole reply** waits for all the words and then cuts them so playback never catches the renderer — it never stalls, but on a model slower than its own audio the wait before the first word grows with the reply. **Speak as it renders** cuts the same words the other way, for the soonest first word and no wait; on a model that cannot keep ahead it stalls between sentences, audibly. **Wait for the whole reply** renders everything in one request before anything plays: the longest wait, and the only reply the model never had to cut. |

A change applies to the next reply; nothing reloads.

**Buffered is the default for every model, deliberately.** There is no
figure to decide for you: a real-time factor belongs to a host, so the app
carries none and shows only what your own machine has measured. Use the
model for a while, read `sensor.<model>_real_time_factor`, switch to automatic,
then watch `sensor.<model>_playback_margin`. How the app decides, the
measurements behind it and what to do when the margin goes negative are in
[Keeping up][streaming].

## How it works

### Streaming

A long reply is normally silent until the last word has been synthesised. With
a model set to automatic, the reply goes to the app over one WebSocket as the
conversation agent writes it, and the app renders it in pieces sized to what
the listener already holds — so the wait before the first word stops growing
with the length of the reply, and a piece is never sent so early that the
next one cannot follow in time. The app measures every request it serves and
paces the next reply from that; this integration only forwards the words and
plays the sound. The reasoning and the measurements are in
[Keeping up][streaming].

Set a model back to buffered if a media player refuses the stream: a player
that probes the file before playing — AirPlay targets do — can give up waiting
for the first bytes of a long reply and fail to open it, while the audio itself
is perfectly fine. Assist pipelines and voice satellites take streams without
trouble.

Each model's **Delivery mode** sensor reports how the app actually spoke the
last reply — whole, streamed or planned — which a model set to automatic decides
per reply. Home Assistant routes every reply through the setting, a
whole message handed to `tts.speak` included, which it wraps as a one-item
stream; the app then knows the whole reply before it has to send anything and
paces it exactly.

When Home Assistant stops reading a reply — the pipeline was cancelled, the
satellite went away — the integration tells the app, and the model stops
rendering at its next step rather than finishing for nobody.

### Talking to the app

- A refusal from the server is reported as the server's own error code and
  message, so the Home Assistant error names the actual cause.
- A rejected key — rotated in the app after setup — starts a re-authentication
  flow by itself; the integration asks for the new key rather than failing every
  reply the same way. On Home Assistant OS the Supervisor re-announces the app
  with the new key, and that updates the entry with nothing to type.
- The limits are on silence from the server rather than on the whole exchange,
  because synthesis is CPU-bound and grows with the text: 10 s to connect and
  180 s between bytes for a reply fetched whole, and 120 s between frames on
  the live socket — a model that has produced nothing for that long is stuck,
  not slow. Other requests to the app time out at 10 s.

## Troubleshooting

- **No voices in the picker.** The model is not downloaded — the entity only
  exists once it is — or it is a cloning model with no reference recording
  uploaded yet. Both are fixed in the app; the entity list follows in seconds.
- **The right words, mispronounced or garbled, in Chinese.** `convert_script`
  was turned off for Traditional Chinese text, or the language tag was not a
  `zh-*` one so conversion never ran. Send `language: zh-TW`.
- **A word is read the mainland way (垃圾 as lā jī).** The language was not
  `zh-TW`, so `taiwan_readings` stayed off. Send `language: zh-TW`, or set the
  option. A word that is still wrong is missing from the app's table — see its
  troubleshooting page.
- **Digits are silent.** Either `normalize_text` was turned off, or the number
  stands on its own — no unit, clock or date around it — which the app leaves
  as digits on purpose, a bare number being as often a room or a phone number
  as a count. Write the unit, or set `expand_numbers: true` under `options:`
  for a call whose numbers are counts. (Hojo cannot say a digit at all, so
  for it the app reads bare numbers by default.)
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
[streaming]: https://github.com/hass-cortex/app-cortex-tts/blob/main/cortex-tts/docs/delivery.md
