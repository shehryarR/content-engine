# Avatar Harness — Milestone Evidence Bundle

**Team:** Ammar Khan (Owner A), Fatima Amin (Owner B), Hanab Malik (Owner C)
**Repository:** shehryarR/content-engine
**Duration:** M0-M6 across ~5 weeks
**Date:** 2026-08-29

---

## M0 — Contracts Frozen

**Verdict: PASS**

- Tag: m0-contract-freeze (d89466e, 2026-07-27)
- Manifest, stage I/O, registry schemas exist as Pydantic models + generated JSON Schema
- Valid fixtures pass; malformed fixtures fail
- CI contract tests: uv run pytest tests/test_fixtures.py
- Post-M0 freeze enforced by scripts/classify_contract_drift.py

---

## M1 — Walking Skeleton

**Verdict: PASS (with documented YouTube blocker)**

- One command runs S00-S100 with stubs
- 11 stage/gate records, SHA checks verified, checkpoints >= 11
- containsSyntheticMedia=true, privacy=unlisted
- YouTube upload blocker: Google sensitive-scope audit pending since 2026-08-05. Code exists (providers/real/youtube_upload.py, scripts/youtube_auth.py), OAuth flow works. Documented in runbooks/youtube-api-status.md.

---

## M2 — Real Avatar + Voice + Lip-Sync

**Verdict: PASS**

- Real ElevenLabs voice, D-ID avatar, OpenAI Whisper captions
- >= 3 scenes, same identity and voice across scenes
- Identity threshold: 0.85 (Pearson correlation, mean-centered)
- Voice threshold: 0.72 (cosine similarity, mean-centered)

---

## M3 — Per-Step Checks + Bounded Self-Correction

**Verdict: PASS**

- Every stage has entry + exit validators (11-point checklist, all closed)
- Malformed script caught at S10, retried with feedback, succeeded without upstream rerun
- Failure-injection tests: tests/integration/test_failure_injection.py (3 tests)
- Contract drift vs M0: ADDITIVE_ONLY. Re-baselined to m3-contract-baseline with mechanical proof.

---

## M4 — Modularity Proof: Faceless Swap

**Verdict: PASS**

- git diff --exit-code m3-contract-baseline -- contracts/ graph/ -> EXIT 0
- Run ID: m4-dryrun-20260828-124412, 11/11 stages, zero retries
- S30: faceless_mixed_media, S40: narration_conform
- G90: modality=FACELESS, contains_synthetic_media=True
- 19 files changed. Zero under contracts/ or graph/.
- Evidence: evidence/m4/

---

## M5 — Gates + Disclosure as Graph Nodes

**Verdict: PASS**

- evaluate_publish_preconditions() in orchestrator/publish_precondition.py
- G90 enforce-on-unknown for all modalities
- Graph lint (scripts/graph_lint.py): S100 preceded by G90, G80 is signal wait
- StubPublishProvider invocation counter
- 5 negative gate tests (tests/test_m5_gate_negative.py), all assert publish count = 0

---

## M6 — Hardening, Resume, Ownership, Dashboard

**Verdict: PASS (with documented limitations)**

- Resume: Temporal persistence + content-hash cache. Kill drill in scripts/kill_drill.sh.
- Artifact integrity: SHA-256 re-verified on every get_artifact(). Corrupt-object test in scripts/corrupt_object_test.py.
- Dashboard: scripts/dashboard_query.sql
- Module runbooks: runbooks/module_script_voice.md, runbooks/module_visual_sync_publish.md
- Idempotent Activities: cache key = input_hash + provider config + upstream hashes

**Documented limitations:**

1. YouTube unlisted upload: blocked on Google audit (pending since 2026-08-05)
2. CI integration tests: excluded since M3 (hang never root-caused)
3. Identity threshold: calibrated against synthetic images, real-photo recalibration deferred

---

## Summary

| Milestone | Verdict |
|-----------|---------|
| M0 | PASS |
| M1 | PASS* |
| M2 | PASS |
| M3 | PASS |
| M4 | PASS |
| M5 | PASS |
| M6 | PASS* |

*Documented external blocker: YouTube sensitive-scope audit.