

cat > runbooks/module_visual_sync_publish.md << 'DOCEOF'



cat > runbooks/module_visual_sync_publish.md << 'DOCEOF'
# Module Runbook — Visual, Sync, QC, Disclosure, Publish (Owner C)

Covers S30 (avatar_render), S40 (media_sync), S70 (quality_control), G90
(disclosure_check), S100 (publish). Owner: Hanab.

---

## S30 — avatar_render

Two providers, same contract (`VisualRequestV1` in, `PrimaryVisualTrackV1`
out, capability `avatar_render`):

| Provider | Modality | File |
|---|---|---|
| `did` | avatar | `providers/real/did_avatar.py` |
| `faceless_mixed_media` | faceless | `providers/real/faceless_mixed_media.py` |
| `stub` | either (fixture playback) | `providers/stub/stub_avatar.py` |

**D-ID (avatar):** uploads the identity reference image + S20 audio to
D-ID temp storage, creates a `/talks` job, polls until done, downloads the
MP4. Needs `AVATAR_RENDER_API_KEY` in `.env` — note the old `DID_API_KEY`
var name is stale, don't use it.

**faceless_mixed_media (faceless):** no external API. Reads the S10
script's per-scene text, renders one FFmpeg `lavfi` solid-colour title
card per scene with the scene text burned in via `drawtext`, concatenates
them. Scene duration is derived from the real S20 audio duration split
evenly across scenes (falls back to 5s/scene if no audio ref present).
Deterministic by design — same input produces the same output hash
(`-map_metadata -1`, `-threads 1`, bitexact flags).

**Font dependency:** `drawtext` needs a real `.ttf` on disk. Set
`FACELESS_FONT_PATH` in your shell profile — there's no bundled default in
this repo. Fails loudly (`FileNotFoundError`) rather than silently
producing broken video if the font's missing.

**Identity threshold check:** `configs/policy/identity_threshold_v1.json`,
`min_score: 0.85`, mean-centered cosine similarity on a 32x32 RGB pixel
grid between the identity reference image and an extracted video frame.
Calibrated against synthetic pairs only (same-identity ~0.994,
different-identity ~0.65-0.78) — recalibrate against real photo pairs
once available, same open item as S20's voice threshold.

**Modality policy — why faceless skips the identity check:**
`configs/policy/modality_validation_policy_v1.json` names which checks
apply to which modality (`S30_identity_check.applicable_modalities:
["avatar"]`). `_validate_avatar_render_stage` in `stage_executor.py`
loads this policy and explicitly skips the identity check for faceless,
logging the skip reason. This is deliberate: a faceless run has no face
to compare against — an unrelated-stills test scored -0.234 against the
0.85 threshold, so every faceless run would burn its full retry budget on
an unwinnable check if this weren't policy-driven. Important: this is
policy-*driven*, not silent-by-omission — the skip only fires when the
policy explicitly says so, not just whenever `identity_ref` happens to be
`None`. Re-running: a `identity_similarity_low` failure on an avatar run
usually means the wrong `reference_asset` was fetched from the registry —
`pipeline.py`'s correction loop re-fetches fresh on retry rather than
resubmitting the same reference.

---

## S40 — media_sync

Two providers, same contract (`VoiceTrackV1` + `PrimaryVisualTrackV1` in,
`SynchronizedMediaV1` out, capability `media_sync`):

| Provider | Modality | File |
|---|---|---|
| `stub` | avatar (pass-through) | `providers/stub/stub_sync.py` |
| `narration_conform` | faceless | `providers/real/narration_conform.py` |

**stub_sync (avatar):** D-ID already muxes audio into its output, so this
is a pure pass-through of S30's video bytes, re-stored with a `sync_`
prefix.

**narration_conform (faceless):** measures S20 audio duration and S30
video duration via `ffprobe`, then either mux-only (durations already
match within 0.1s), pad (video shorter — `tpad` clones the last frame),
or trim (video longer — `-t`) before muxing. Note: the pad/trim branches
include `-threads 1` for determinism; the mux-only branch does not — this
hasn't caused an observed hash mismatch in testing, but if you see a
flaky determinism failure specifically on the mux-only path, that's the
first place to look.

**Duration tolerance:** `configs/policy/sync_policy_v1.json`,
`tolerance_seconds: 2.0`. Formalizes what used to be a hardcoded constant
in `stub_sync.py`.

**Artifact-id convention:** downstream stages (assembly) find S40's
output by artifact_id prefix `sync_`, not just mime type — matching a
video ref that *isn't* prefixed `sync_` means you're looking at S30's raw
output, not S40's.

---

## S70 — quality_control

`providers/stub/stub_qc.py` (deterministic, always runs) +
`providers/real/qc_model_judge.py` (vision-LLM judge, subjective).

**Deterministic checks (stub_qc.py) — only Layers 1 and 4 exist:**
stream presence, non-empty output, codec/resolution match against
hardcoded D-ID-shaped expectations (`h264`/`aac`, min 512x512 — flagged in
the file itself as needing a real config once one exists), and a Layer 4
lip-sync *proxy*: audio/video duration match within 0.75s, not real
lip-sync. Layers 2/3/5 (identity, voice, vision similarity) were never
implemented here — that's intentional scope, not a gap, per the M4 audit.
Modality-neutral: none of these checks assume a face.

**Model judge (qc_model_judge.py):** samples 3 frames + caption text,
asks a vision LLM to flag rendering glitches, broken framing, or garbled
captions — explicitly told not to flag compression softness, lighting, or
script content. The flagging criteria themselves are modality-neutral.
The only avatar-specific wording was the system prompt's opening line
("reviewing a short AI-avatar video") — reworded to be modality-neutral,
per the M4 Day 1 audit finding.

---

## G90 — disclosure_check

`providers/stub/stub_disclosure.py` produces the `DisclosureDecisionV1`
(always `contains_synthetic_media=True` in stub mode, reading modality
from the S00 idea artifact when present). `_validate_disclosure_stage` in
`stage_executor.py` is the actual gate.

**Enforce-on-unknown (the M4 fix):** the check enforces
`contains_synthetic_media == True` for every modality *except* those
explicitly listed in `_MODALITIES_EXEMPT_FROM_SYNTHETIC_DISCLOSURE` —
which is intentionally empty. A modality nobody's thought about yet fails
closed by default, not open. This replaced an earlier version that only
enforced for `modality == "AVATAR"`, under which a faceless run with a
synthetic voice and `contains_synthetic_media=False` sailed through G90
green (S100's own unconditional check caught it downstream, so nothing
actually got published — but the gate meant to catch it wasn't catching
it).

**The D3+D4 ordering lesson:** this fix depended on landing in the same
commit window as the provider-side fix (D3) — fixing the validator alone
without the provider, or vice versa, would have left a window where one
side assumed the other was already correct. Worth remembering if G90 or
its upstream provider ever get touched independently again: check both
sides land together, don't assume a partial fix is safe to ship alone.

`policy_basis` field: records which policy version made the disclosure
call, currently `"policy_stub_g90"` in stub mode — not yet wired to a
real versioned policy file the way S30/S40's thresholds are.

---

## S100 — publish

`providers/stub/stub_publish.py`, capability `publish`. Dry-run only —
`dry_run=True` is hardcoded into every `PublishReceiptV1`, and
`TARGET_PRIVACY = "unlisted"` is asserted, not configurable.

**Its own unconditional disclosure check:** re-reads the G90 disclosure
artifact and refuses to publish if `contains_synthetic_media` is falsy —
this check does **not** gate on modality the way G90 used to. Confirmed
via the M4 Day 2 D5 correction: S100 was originally documented as
"inheriting G90's gap," which was wrong — it's an independent,
unconditional backstop. Don't re-introduce a modality condition here even
if G90 ever grows one; that would remove the one check that doesn't
depend on G90 being correct.

**Invocation counter (M5 step 7):** `StubPublishProvider._invocation_count`,
class-level, with `reset_count()`/`get_count()`. Added so the M5 negative
gate suite (`tests/test_m5_gate_negative.py`) can assert publish was never
actually reached across all 5 gate-blocking scenarios. Class-level, not
instance-level — call `reset_count()` in test setup or counts leak across
tests sharing the class. Increments at the top of `run()`, before the
disclosure check — it measures "was `run()` invoked," not "did publish
succeed."

**YouTube blocker (stated limitation, not an engineering gap):** real
upload is fully built (`providers/real/youtube_upload.py`, OAuth flow
tested, adapter code complete) but gated behind Google's sensitive-scope
audit, submitted 2026-08-05, pending since. Full status:
`runbooks/youtube-api-status.md`. Operational rule until approved: S100
stays dry-run only in every environment, no exceptions.

---

## Known catalog quirk (not a bug, but confusing)

`orchestrator/registry.py`'s `_PROVIDER_CATALOG` dict defines both
`avatar_render` and `media_sync` twice (once with just `stub`+`did`/
`stub`, again with the faceless providers added). Python dict literals
silently let the second definition win, so the effective catalog is
correct — but the first block is dead code. If you're adding a new
provider to either capability, add it to the *second* occurrence, and
consider deleting the first block in a cleanup pass so it doesn't look
like two independent registrations.

---

## Quick reference — where things live

| What | Where |
|---|---|
| S30 providers | `providers/real/did_avatar.py`, `providers/real/faceless_mixed_media.py`, `providers/stub/stub_avatar.py` |
| S40 providers | `providers/real/narration_conform.py`, `providers/stub/stub_sync.py` |
| S70 providers | `providers/stub/stub_qc.py`, `providers/real/qc_model_judge.py` |
| G90 provider | `providers/stub/stub_disclosure.py` |
| S100 provider | `providers/stub/stub_publish.py` |
| All 5 validators | `orchestrator/stage_executor.py` (`STAGE_VALIDATORS` dict) |
| Frozen thresholds/tolerances | `configs/policy/identity_threshold_v1.json`, `configs/policy/sync_policy_v1.json`, `configs/policy/modality_validation_policy_v1.json` |
| YouTube status | `runbooks/youtube-api-status.md` |
