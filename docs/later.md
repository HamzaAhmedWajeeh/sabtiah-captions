# Later

Ideas noticed while building, deliberately not built. Nothing here is in
BUILD_SPEC.md.

- Probe: a `--compare` mode diffing two captures of the same audio under
  different configs (e.g. `two_way` vs `one_way`, endpoint sensitivity levels),
  so Phase 7 tuning is a diff rather than two reports read side by side.

- A line-aligned "both" view (BUILD_SPEC's Option A) is now *supported by
  evidence* — Phase 1 showed translations never arrive after `<end>`. It was
  not built because `<end>` boundaries range from 0.1 s to 300 s, so the pairs
  would be unusable as caption lines. If it is ever wanted, the missing piece
  is pairing: `captions.py` gives paragraph ids per stream and deliberately
  does not align them across languages, because the two sides break at
  different points.
- `tools/render_capture.py` could diff two renderings of the same capture under
  different `--max-chars`, to tune the soft paragraph break without guessing.

- `/screen` sizes text with a fixed `min(5.5vw, 9vh)` and `lines` counts
  *paragraphs*, so a 360-character paragraph fills more than one screenful and
  the top is masked off. Fitting the last N paragraphs to the frame (binary
  search on font size, or a per-paragraph character budget for this view) would
  make `lines=2` mean what it looks like it means. Tune `lines` by eye at
  rehearsal instead, for now.
- The audience page has no way to see the other language for a single
  paragraph without switching views. A tap-to-reveal on one paragraph would
  need the cross-stream pairing that `captions.py` deliberately does not do.
