# Hojo TTS for Home Assistant

Home Assistant integration for the [Hojo TTS app](https://github.com/hass-cortex/app-hojo-tts) —
on-device, CPU-only text-to-speech.

Adds one TTS entity per model downloaded on the server — a model with no
weights on disk would be a permanently-failing voice in the picker — so Hojo
voices can be selected in any Assist pipeline or `tts.speak` action.

## Setup

Install the **Hojo TTS** app first. The integration is then discovered
automatically: **Settings → Devices & services → Hojo TTS → Configure**.

To add it by hand, use the app's address (`http://homeassistant.local:8771`)
and the API key from the app's Configuration tab.

## Speaking

The entity is the target; everything the engine itself understands goes in
`options`.

```yaml
action: tts.speak
target:
  entity_id: tts.hojo_tts_light_40m
data:
  media_player_entity_id: media_player.kitchen_speaker
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

## Traditional Chinese

The underlying model cannot pronounce Traditional Chinese glyphs — measured at
32% character error rate against 4% once converted to Simplified. It also has
no text normalisation, so `26.5°C` and `14:35` come out as noise.

Both are fixed on the server before synthesis, and are on by default. The
`normalize_text` and `convert_script` options exist for callers whose text is
already prepared; turning them off for ordinary Traditional Chinese will make
the voice unintelligible.

## Per-sentence synthesis

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

## Options

Entry options, under **Configure**:

| Option | Default | Meaning |
|--------|---------|---------|
| `stream_models` | models fast enough | Which models speak each sentence as it is written |

Per-call options on `tts.speak`:

| Option | Default | Meaning |
|--------|---------|---------|
| `voice` | server default | Voice id — a built-in voice or a cloned one |
| `preferred_format` | `wav` | `wav`, `flac` or `ogg`; a streamed reply is always WAV and converted by Home Assistant |
| `audio_output` | `wav` | The same three values, read only when `preferred_format` is absent |
| `normalize_text` | `true` | Expand numbers, units, dates and clock times, in the script of the text |
| `convert_script` | Chinese languages only | Convert Traditional glyphs to Simplified |
