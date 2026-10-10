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
