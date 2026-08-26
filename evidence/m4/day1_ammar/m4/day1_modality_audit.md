# M4 Day 1 — Modality Audit (Owner C / Hanab)

One row per validator check, S30 through S100. Each marked avatar-assuming
or modality-neutral, based on reading the actual validator code and, where
relevant, running it against real inputs — not predicted from the doc.

## Part 1 — S30 identity check

**Test performed:** ran the actual mean-centered cosine similarity used by
`compute_identity_similarity()` on two unrelated stock stills (not the same
identity, not a fixture pair).

**Result:** score = **-0.234**. Threshold is 0.85.

**Finding:** faceless output will **fail every run**, not spuriously pass.
`identity_similarity_low` is in `RETRYABLE_VALIDATION_FAILURE_TYPES`, so
every faceless run burns its full retry budget on an unwinnable check
before dying — the failure will look like a real quality problem, not a
configuration one.

**Fix location:** needs a policy or provider mechanism that skips this
check entirely for faceless modality. Cannot live in `contracts/` or
`graph/`.

## Part 2 — S40 sync check

Read `_validate_sync_stage` and its underlying `validate_sync()`
(`providers/stub/stub_sync.py`) in full.

**Finding:** the entire check is duration-tolerance only — compares S40
output duration against S20 audio duration within `tolerance_seconds`.
There is no lip-sync, mouth-region, or any face-dependent logic anywhere
in this function.

**Verdict: fully modality-neutral.** Applies unchanged to faceless. No fix
needed.

## Part 3 — S70 QC + G90 disclosure

### S70 deterministic checks (`stub_qc.py`)
Read the full `run()` method and its metrics dict.

**Finding:** metrics are stream presence (video/audio), duration,
codec/resolution, and a duration-based `sync_score` proxy (same mechanism
as S40's Layer 4). No identity, voice, or face-dependent metric exists in
this file — Layers 2/3/5 (identity/voice/vision-LLM) from the original M2
QC design were never implemented here, per the file's own comment.

**Verdict: modality-neutral.** No fix needed.

### S70 model judge (`qc_model_judge.py`)
Read `_JUDGE_SYSTEM_PROMPT` in full.

**Finding:** the actual flagging criteria (rendering glitches, broken
framing, garbled captions) are modality-neutral. Only the prompt's opening
line ("You are reviewing a short AI-avatar video...") assumes avatar —
wording only, not a functional gate.

**Verdict: mostly neutral, trivial fix needed.** Reword the prompt's
opening line to be modality-generic. Lives entirely in provider-private
code (`qc_model_judge.py`), not a contract. Scheduled for Day 2/3, not
today (no provider code today per the doc).

### G90 disclosure — real gap, not just avatar-assuming

Read `_validate_disclosure_stage` in full.

**Finding:** `contains_synthetic_media == True` is only enforced when
`modality == "AVATAR"`. For any other modality (including faceless), the
function falls straight through to `passed=True` regardless of the flag's
actual value — **a faceless run with `contains_synthetic_media=False`
would currently pass G90 with zero enforcement.**

This is the dangerous "defaults to false / skips the check silently"
scenario the M4 doc explicitly warned to check for. Raising to Ammar and
Fatima today, since it touches M5's disclosure track directly.

**Fix location:** needs a written `policy_basis` decided deliberately
(not defaulted), and enforcement extended to cover non-avatar modality.
Lives in `orchestrator/stage_executor.py` + registry policy profiles —
not a contract change.

### S100 publish
Read `_validate_publish_stage` in full.

**Finding:** checks privacy value + re-reads the upstream G90 disclosure
decision's `contains_synthetic_media`. The check itself is modality-
neutral, but it inherits G90's gap indirectly — if G90 lets a False flag
through for faceless, S100's re-check of that same flag also passes it.

**Verdict: fixed by fixing G90.** No separate S100 fix needed.

## Audit Table Summary

| Stage | What it checks | Avatar-assuming or neutral | Where faceless behavior lives |
|---|---|---|---|
| S30 | Identity similarity (mean-centered pixel correlation) | **Avatar-assuming** — confirmed, real test scored -0.234 | Policy/provider mechanism to skip for faceless |
| S40 | A/V duration tolerance only | **Modality-neutral** — confirmed | No change needed |
| S50 | Caption timing vs audio duration | Neutral (audio/duration-based) | No change needed |
| S60 | Stream presence, duration match | Neutral | No change needed |
| S70 (deterministic) | Stream presence, duration, codec/resolution, sync_score proxy | **Modality-neutral** — confirmed, no identity/voice/face metric implemented | No change needed |
| S70 (model judge) | Subjective vision-LLM quality check | Mostly neutral — flagging criteria are fine, only prompt wording assumes avatar | Reword prompt, Day 2/3, provider-private |
| G90 | `contains_synthetic_media` enforcement | **Real gap** — only enforced for AVATAR modality; non-avatar currently passes regardless of flag value | Needs `policy_basis` decision + enforcement extension — raised to Ammar/Fatima today |
| S100 | Privacy + re-reads G90 decision | Neutral itself, inherits G90's gap indirectly | Fixed by fixing G90 |

## Mechanism recommendation

S30 and S40 both need modality-conditional behavior, but via **the same
mechanism** — the doc explicitly flags "two different mechanisms" as a
sign something's wrong. Proposing: a policy profile field (e.g.
`applicable_modalities` or similar) on each check, read by
`stage_executor.py` at validation time, rather than a per-check code
branch. This keeps modality logic in policy/provider config, never in
`contracts/` or `graph/`. Final mechanism choice to be confirmed with
Ammar given his Part 3 seam audit findings.
