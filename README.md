# Sabtiah Live Captions

Live Arabic/English captions for a meetup talk. A laptop takes the room's
audio, streams it to [Soniox](https://soniox.com) for transcription and
translation, and serves captions to phones on the same network — everyone
reads along in whichever language they prefer. Arabic speech shows English
captions and vice versa; that inversion is the point.

One Python process, no database, no build step, no frontend framework.

- **`BUILD_SPEC.md`** is the plan and the contract.
- **`docs/CHECKPOINT.md`** is what is done, what is next, and what is broken.
- **`docs/soniox-notes.md`** is what the API actually does, measured.

> **Status:** phases 0–5 of 9 are done. Captions work end to end from a live
> microphone, including reconnects. The QR code and the public tunnel
> (`PUBLIC_URL` in `.env`) are **phase 8 and not built yet** — today the
> phones use the laptop's address on the local network, below.

## Requirements

- Windows or macOS (Windows is the primary target)
- **Python 3.11+** and [**uv**](https://docs.astral.sh/uv/)
- A **Soniox API key** — <https://soniox.com>
- A **microphone**. A USB interface fed from the room's mixer is the goal; a
  headset or the laptop's built-in mic is fine for testing.
- **ffmpeg**, only if you want to feed audio files through the pipeline
  (`winget install Gyan.FFmpeg`, or `brew install ffmpeg`). The live
  microphone path does not use it.

## Setup

```bash
git clone https://github.com/HamzaAhmedWajeeh/sabtiah-captions.git
cd sabtiah-captions
uv sync
cp .env.example .env        # Windows: copy .env.example .env
```

Then edit `.env` and fill in two values:

```ini
SONIOX_API_KEY=your-key-here
ADMIN_TOKEN=a-long-random-string
```

Generate the admin token — hex, so it is safe in a URL:

```powershell
# Windows PowerShell
[Guid]::NewGuid().ToString('N')
```

```bash
# macOS
openssl rand -hex 16
```

`.env` is gitignored. The key never leaves the laptop — it is not sent to the
browser, and a test checks that no served file mentions it.

## Run it

Bind to all interfaces so phones can reach the laptop:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Find the laptop's address on the local network:

```powershell
# Windows
Get-NetIPAddress -AddressFamily IPv4 | Where-Object PrefixOrigin -eq 'Dhcp'
```

```bash
# macOS
ipconfig getifaddr en0
```

On Windows, allow the port once (elevated PowerShell). Without this the phones
get nothing, and the failure looks like the server being down:

```powershell
New-NetFirewallRule -DisplayName "Sabtiah captions 8000" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 `
  -Profile Private,Public
```

Then:

| Who | URL |
| --- | --- |
| Audience phones | `http://<laptop-ip>:8000/` |
| Big screen behind the speaker | `http://<laptop-ip>:8000/screen?lang=en&lines=2` |
| You, the operator | `http://<laptop-ip>:8000/admin?token=<ADMIN_TOKEN>` |

`/admin` is never linked from the audience page. Audience pages are read-only.

## Start a live session

**This spends money.** Everything else in this project is free.

1. Open `/admin?token=...`.
2. Set **Source** to *Microphone — audio only* and pick your input device.
   Watch the **level meter** while someone talks: it should sit in the middle
   of the bar and touch the first mark on peaks. If **CLIPPING** lights, turn
   the mixer down — nothing later can undo it.
3. Set **Source** to **Live captions**, tick **"I mean it"**, and press
   **Start**. Captions appear on the phones within a second or two.
4. Press **Stop** when the talk ends. The stream stops with it.

Tick **"Log raw responses"** if you want the run saved to `logs/` — it replays
later as a test fixture, and it is the only way to answer a question about the
output after the fact.

If the network drops, the session recovers by itself: it reconnects with
backoff, re-sends up to 10 seconds of buffered audio, and if more than that
was lost it writes `[connection interrupted]` into both streams rather than
letting the captions silently skip. See `docs/resilience-test.md`.

## Session details on the day

Speaker names, the talk title, companies, notes, and any vocabulary specific
to this talk go in the **This talk** box on `/admin`. One name per line.

- **Save** writes them and uses them at the next **Start**.
- If a session is already running, Save shows *"Changes saved, not applied
  yet"* and an **Apply now** button. Apply now reconnects in the background:
  the audience sees nothing, no audio is lost, and the captions carry on in
  the same place. Use it when a speaker changes mid-session.
- **Clear session details** empties everything.
- Extra terms are added to the shared `config/glossary.yaml` vocabulary, not
  instead of it. The page lists what the glossary already covers so you do
  not retype it.

**These stay on this laptop.** They are written to `config/session.yaml`,
which is **gitignored** — speaker names are not public and this repository is.
They go to exactly two places: the transcription API, where they are what
makes names come out spelled right, and `/admin` behind its token. They never
appear on the audience page, the big screen, the viewer socket, or any log;
those carry counts at most, and `tests/test_session_privacy.py` checks it.

`config/session.example.yaml` is the committed shape. A fresh clone has no
`session.yaml` at all, which is fine — everything starts empty.

## Testing without spending anything

Replay a saved capture through the whole pipeline — real captions, real
timing, no network:

```bash
# with the server running, in another terminal
curl -X POST "http://127.0.0.1:8000/admin/start?token=<ADMIN_TOKEN>&capture=run1.jsonl&paced=true"
curl -X POST "http://127.0.0.1:8000/admin/stop?token=<ADMIN_TOKEN>"
```

Or from `/admin`: **Source → Replay a capture**, pick `run1.jsonl`, Start.
`paced=true` runs at real speed, so you can judge the page as the audience
will. `paced=false` dumps a whole capture in under a second.

Render a capture as plain text, no server needed:

```bash
uv run python tools/render_capture.py tests/fixtures/run1.jsonl --stats
```

Run the tests — **all 393 are offline**, none touches the network:

```bash
uv run pytest
```

## Before a session

- [ ] Laptop **charged** and plugged in.
- [ ] Laptop on a **phone hotspot**, not venue Wi-Fi. Venue networks block
      device-to-device traffic and the phones will not reach the laptop.
- [ ] Microphone tested: `uv run python tools/list_devices.py --listen 10`
      and watch the level while someone speaks.
- [ ] `config/glossary.yaml` has the talk's technical vocabulary. Terms listed
      there stay in English inside the Arabic captions — this is measured and
      it works.
- [ ] **This talk** filled in on `/admin` with the **speaker names**. It is
      empty by default, and names are where recognition fails.
- [ ] Firewall rule added, and the audience URL **opened once on a phone**.
- [ ] Check the spend on `/admin` before you start.

## Troubleshooting

**The level meter never moves.** Wrong input, a muted device, or Windows
blocking microphone access to the terminal (Settings → Privacy & security →
Microphone). `uv run python tools/list_devices.py` lists every input; on
Windows the same microphone appears several times, once per audio API, and the
WASAPI entry is usually the one to pick.

**A phone cannot connect.** Check it is on the same network; check the
firewall rule above; check the laptop's IP has not changed (it is DHCP, so it
changes). `http://<laptop-ip>:8000/healthz` should return `{"ok":true}` from
the phone's browser.

**`ffmpeg is not on PATH` right after installing it.** Open a new terminal.
PATH is read when the terminal starts.

**`/admin` returns 401.** The token in the URL does not match `ADMIN_TOKEN` in
`.env`, or the server was started before `.env` was edited — restart it. An
unset `ADMIN_TOKEN` denies everything rather than allowing it.

**The server will not start: "only one usage of each socket address".**
Something is already on port 8000, usually an older copy of this server:

```powershell
(Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

## Costs

Soniox bills **per minute of audio streamed**, so only live sessions cost
anything. Replay, the tests, the level meter and the file tools are all free —
nothing in them can open a connection.

Audio re-sent after a reconnect is billed again, so a flaky network costs a
little more than the talk's length.

Every run is logged to `logs/usage.jsonl`, and `/admin` shows the running
total. Set `SONIOX_RATE_PER_MINUTE` in `.env` to see an estimate in dollars;
without it the page shows minutes only, which are counted rather than guessed.
