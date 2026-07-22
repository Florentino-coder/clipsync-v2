# OCR Gate 1 — Manual accuracy check (remaining)

Manual Gate 1 (≈20 real slips on device, ML Kit Latin) is **not done** in Task 1.3.

Remaining:
- Capture ~20 real slips (SCB / KBank / BBL mix) on hardware
- Run ML Kit Latin OCR and compare amount / last4 / ref_number vs ground truth
- Replace synthetic fixtures (`scb_01.txt`, `kbank_01.txt`, `bbl_01.txt`) with real Latin OCR samples when Gate 1 passes

Until then, unit tests validate the pipeline with injected fake OCR only. Thai name accuracy is PC-side EasyOCR (later task), not part of this gate.
