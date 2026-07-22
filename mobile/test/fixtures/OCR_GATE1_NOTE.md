# OCR Gate 1 — Manual accuracy check (SKIPPED)

Task 1.3 Step 5 defers the manual OCR accuracy gate to a later milestone.

Current parser fixtures (`scb_01.txt`, `kbank_01.txt`, `bbl_01.txt`) are **synthetic stub OCR text**, not real ML Kit output from device captures. Replace them with real Latin OCR samples when Gate 1 is run on hardware.

Until then, unit tests validate the pipeline with injected fake OCR only.
