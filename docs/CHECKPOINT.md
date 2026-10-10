# Checkpoint — 2026-10-11

Six days to the demo (Saturday Oct 17). Phases 0–2 done, Phase 3 next.

## Done

- **Phase 0** — skeleton, `pyproject.toml`, `.gitignore`, FastAPI stub.
- **Phase 1** — all five section 4 questions answered from five live captures.
  Findings in `docs/soniox-notes.md`; **read it before touching caption
  logic.** Two probe bugs fixed along the way (cp1252 crash on Arabic to a
  redirected stdout; a hang + leaked ffmpeg on any early-ending stream).
- **Phase 2** — `app/captions.py` (pure, Option B, two per-language streams)
  and `app/replay.py` (`FakeSonioxClient` over saved JSONL).
  `tools/render_capture.py` renders a capture as the audience would see it.

**69 tests green, all offline** — 53 captions, 10 probe analyzer, 6 probe
shutdown. No test touches the network.

## Soniox audio spent: 22.0 minutes

300.01 s × 4 (run1, run2, run3, run4) + 120.0 s (run5). Failed attempts sent
~0.2 s each and are noise. Credit is $10 for the whole project; Phase 2 cost
nothing and Phase 3 should cost nothing.

## Next: Phase 3

Server, broadcast, audience page — `app/main.py`, `app/broadcast.py`,
`web/index.html` + `app.js` + `style.css`, plus `/screen` and `/admin`.
Build it entirely against `FakeSonioxClient`; `replay.py` already paces a
capture at real time (`paced=True`) so the page can be judged at true speed,
caption lag included.

Two contract details `captions.py` documents and the hub must respect:

- Paragraph ids are **globally unique, not aligned across streams** — the two
  languages break at different points under Option B.
- **Trimming is invisible in a diff.** A client must treat a snapshot as
  authoritative and drop any id missing from it.

## Open items

1. **`translation_terms` is untested, and it is the highest-value experiment
   left.** In the `en`→`ar` direction *nothing* stays in Latin script — zero
   Latin runs survived into the Arabic captions, so `RAG`/`MCP`/`LLM` will be
   transliterated and near-unreadable for the demo audience.
   `config/glossary.yaml` already carries the intended fix. Needs ~60 s of
   English containing the glossary terms. Until then, treat Latin-script
   preservation in Arabic captions as **not working**.
2. **Saudi dialect is still unmeasured.** `recordings/saudi-5min.m4a` turned
   out to be MSA news register about Yemen with zero distinctively Saudi lexis,
   so it answers nothing. Needs casual Saudi conversation, not broadcast. The
   audience is Saudi; the only dialect actually measured is Egyptian.
3. **Live microphone capture untested.** Every capture came through ffmpeg from
   a file. `MicSource` and the `sounddevice` callback are Phase 4.
4. **Long sessions untested.** The 300-minute cap, the 280-minute rollover and
   the 20 s keepalive have never run — audio flowed continuously, so no
   keepalive was ever needed.
5. **`config/session.yaml` is empty.** Filling `speakers` would likely have
   fixed `جودت` → "Goudat" in run1. Cheap win, no API cost.

## Half-finished

Nothing in the code. One loose end worth knowing:

- **Some Phase 1 numbers are not reproducible from the repo.** The committed
  `tools/probe_soniox.py --analyze` covers the five questions, but the
  per-distinct-token delay figures, the attribution-free `<end>` check and the
  word-boundary dialect counts came from throwaway scripts in a session
  scratchpad that is **not** in git. The conclusions are recorded in
  `docs/soniox-notes.md` with their inputs, but re-deriving them means
  rewriting those scripts. Folding them into `tools/` as an `--analyze2` or a
  second tool would fix it; not done, and not required by the spec.
