# Phase 1 run sheet

Both inputs must exist first (check from the project root):

    ls -la .env recordings/test-sentences.m4a

Then, in order:

    # 1. two-way ar/en, endpoint detection on (BUILD_SPEC section 3 config)
    uv run python tools/probe_soniox.py \
        --file recordings/test-sentences.m4a \
        --out docs/captures/run1.jsonl

    # 2. one-way to English, the Phase 6 fallback mode
    uv run python tools/probe_soniox.py \
        --file recordings/test-sentences.m4a \
        --config config/oneway-en.json \
        --out docs/captures/run2-oneway.jsonl

    # 3. analyze both
    uv run python tools/probe_soniox.py --analyze docs/captures/run1.jsonl \
        --report docs/captures/run1.md
    uv run python tools/probe_soniox.py --analyze docs/captures/run2-oneway.jsonl \
        --report docs/captures/run2-oneway.md

    # 4. keep them as Phase 2 fixtures
    cp docs/captures/run1.jsonl docs/captures/run2-oneway.jsonl tests/fixtures/

The recording is 10 scripted sentences with 1-2 s pauses: 1-3 Saudi Arabic,
4-6 English, 7-10 mixed Arabic with English tech terms. The script is in
docs/soniox-notes.md for output comparison. Each pass streams at real time, so
budget roughly the recording's duration per run.
