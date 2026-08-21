# Registry DB Update — voice_synthesis fix + typo cleanup

Everyone needs to run this once against their local Postgres.

## 1. Pull the migration

```bash
git pull
```

## 2. Confirm your postgres container name

```bash
sudo docker ps --filter "name=postgres"
```

(Container name varies per machine — use whatever shows up, not necessarily `temporal-postgresql`.)

## 3. Run the migration

```bash
sudo docker exec -i <your-postgres-container> psql -U temporal -d content_engine < sql/alter_registry_profiles_v2.sql
```

This does three things:
- Adds `provider_voice_id` to `voice_profiles` — the real ElevenLabs voice ID the provider needs to call `.convert()` with, separate from the registry's own `voice_id` consent identifier.
- Renames the `refrence_sample_id` typo → `reference_sample_id` on `voice_profiles`, and → `reference_asset` on `identity_profiles` (matching each table's actual Pydantic contract field name).
- Seeds `voice_001` with `provider_voice_id = onwK4e9ZLuTAKqWW03F9` — a real, API-callable ElevenLabs premade voice confirmed to clear the free-tier 402 restriction. **If you're using your own separate ElevenLabs API key rather than a shared one, this specific ID might not be in your account's voice list** — run the check below before assuming it works for you.

Safe to re-run — every statement is idempotent.

## 4. Seed your identity reference image

Rather than a placeholder row, run this with a real reference image (JPEG/PNG, under 10MB, Google Drive link or direct URL):

```bash
uv run python -m scripts.seed_identity_reference identity_001 "Mona Lisa" "https://drive.google.com/file/d/116eoGRk3k0KUOCCBSyozGOv4NUGiYScA/view?usp=sharing"
```

This downloads the image, validates it, stores it as a content-addressed artifact via `put_artifact`, and seeds `identity_profiles` with `consent_status = 'active'`.

## 5. Verify

```bash
sudo docker exec -it <your-postgres-container> psql -U temporal -d content_engine -c "
\d voice_profiles
\d identity_profiles
SELECT voice_id, provider_voice_id, consent_status FROM voice_profiles;
SELECT identity_id, reference_asset, consent_status FROM identity_profiles;
"
```

Expect: no `refrence_sample_id` column on either table anymore; `voice_profiles.provider_voice_id` populated; `identity_profiles.reference_asset` pointing to a real stored artifact path.

## 6. If your own ElevenLabs key rejects `onwK4e9ZLuTAKqWW03F9`

Check which voices your specific account can call:

```bash
uv run python -c "
from providers.elevenlabs_voice import ElevenLabsVoiceProvider
p = ElevenLabsVoiceProvider()
voices = p._client.voices.get_all()
for v in voices.voices:
    print(v.voice_id, v.name)
"
```

Pick any `category: premade` entry from that output and update your own `voice_profiles.provider_voice_id` accordingly — Voice Library voices you haven't explicitly added will 402 on free tier regardless of which ID you try.

---

## S20 Speaker Similarity Threshold & Recalibration Guide

We use a spectral cosine similarity validator in stage S20 to ensure the generated audio sounds like the registered voice reference.

### 1. Current Calibration Configuration
The threshold is frozen in `configs/policy/voice_threshold_v1.json` at **0.72**. 
- **Accepted reference/generation pairs** (same voice) score **≥ 0.80**.
- **Negative reference/generation pairs** (different voice) score **≤ 0.55**.
- **Why No Mean-Centering:** Cosine similarity runs directly on the raw, non-negative power spectra to match the frequency envelope shape. Mean-centering would change the math (turning it into a correlation metric) and invalidate the calibrated threshold.

### 2. Recalibration Steps (When upgrading ElevenLabs models)
If we upgrade or change the underlying voice generation model, the spectral characteristics of the generated audio may shift, requiring recalibration:

1. **Prepare test samples:**
   - Put a reference voice sample and a generated sample from the *same* voice in the accepted test set.
   - Put the same reference voice sample and a generated sample from a *different* voice in the negative test set.
2. **Calculate scores:**
   - Run the similarity metric offline:
     ```python
     from providers.real.elevenlabs_voice import compute_speaker_similarity
     # Load bytes for reference, accepted, and negative clips
     print("Same speaker score:", compute_speaker_similarity(ref_bytes, acc_bytes))
     print("Diff speaker score:", compute_speaker_similarity(ref_bytes, neg_bytes))
     ```
3. **Update Policy:**
   - Set the new threshold `min_score` in `configs/policy/voice_threshold_v1.json` as the midpoint between the minimum accepted score and the maximum negative score.