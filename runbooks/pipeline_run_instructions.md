# Avatar Harness — Local Setup Runbook

Follow this **exactly, in order**. Every step here has been run and verified
against a genuinely fresh environment. Do not skip steps or reorder them —
several early failures during setup came from steps being run out of order.

---

## Prerequisites

Before starting, confirm you have:

```bash
docker compose version
uv --version
ffmpeg -version && ffprobe -version
```

If `ffmpeg` is missing:

```bash
sudo apt update && sudo apt install -y ffmpeg
```

`ffmpeg`/`ffprobe` are **required host binaries** — the app runs on your
machine, not inside a container, and several stages (assembly, identity
similarity, speaker similarity) shell out to them directly.

---

## Step 1 — Clone and check the SQL init scripts

```bash
git clone <repo-url>
cd content-engine
ls -la sql/
```

Confirm every file listed is a **real file**, not a directory. A common
mistake is a broken download leaving an empty directory where a `.sql`
file should be — Docker will silently mount that as a directory instead of
erroring, and Postgres will fail with `could not read from input file: Is
a directory`. If anything under `sql/` looks wrong, re-pull from git rather
than trying to patch it locally.

---

## Step 2 — Full clean rebuild

```bash
sudo docker compose down -v
sudo docker compose up -d
sleep 15
sudo docker compose ps -a
```

`down -v` removes volumes too — this only matters if you have prior local
data you care about (you shouldn't, on a first setup).

**Every container should show `Up` or `Exited (0)`.** `minio-createbucket`
exiting with code `0` is expected — it's a one-shot job. Anything showing
`Exited (1)` or `unhealthy` means something went wrong — see
[Troubleshooting](#troubleshooting) below before continuing.

---

## Step 3 — Verify containers actually started cleanly

```bash
sudo docker logs temporal-postgresql --tail 20
sudo docker logs temporal --tail 20
```

You should **not** see:
- `database "temporal" does not exist`
- `Is a directory`
- Repeated `FATAL: the database system is starting up` past the first ~15
  seconds

If you see any of these, go to [Troubleshooting](#troubleshooting).

---

## Step 4 — Verify database schema

```bash
sudo docker exec temporal-postgresql psql -U temporal -d content_engine -c "\l" | grep -i temporal
sudo docker exec temporal-postgresql psql -U temporal -d content_engine -c "\dt"
sudo docker exec temporal-postgresql psql -U temporal -d content_engine -c "\d identity_profiles"
sudo docker exec temporal-postgresql psql -U temporal -d content_engine -c "\d voice_profiles"
```

Expected:
- `temporal` and `temporal_visibility` databases both listed
- Four tables: `identity_profiles`, `voice_profiles`, `manifest_stage_records`, `stage_run_records`
- `identity_profiles` has `reference_asset` (NOT NULL) and `reference_sample_hash` (NOT NULL)
- `voice_profiles` has `reference_asset` and `reference_sample_hash` (both nullable)

If any of these are missing, **do not try to patch the schema by hand** —
go back to Step 2 and do a genuinely clean rebuild. Manual `ALTER TABLE`
patches on a half-built schema are how earlier setups here went sideways.

---

## Step 5 — Configure `.env`

```bash
cp .env.example .env
```

**Only do this if `.env` doesn't already exist.** If you already have one
with real keys in it, edit it manually instead — don't overwrite it.

Fill in real API keys:

```
SCRIPT_GENERATION_PROVIDER=openai        # or "gemini" — must match the model below
SCRIPT_GENERATION_API_KEY=<your key>
SCRIPT_GENERATION_MODEL_ID=gpt-4.1-mini  # or gemini-3.6-flash if using gemini

VOICE_SYNTHESIS_API_KEY=<your ElevenLabs key>
AVATAR_RENDER_API_KEY=<your D-ID key>
CAPTION_GENERATION_API_KEY=<your OpenAI key>   # required regardless of script provider — captions are hardcoded to OpenAI
```

**`SCRIPT_GENERATION_PROVIDER` and `SCRIPT_GENERATION_MODEL_ID` must match
each other.** Setting a Gemini model without setting the provider to
`gemini` will silently send the request to OpenAI and fail with a
confusing 404.

---

## Step 6 — Install dependencies

```bash
uv venv && uv sync
```

---

## Step 7 — Seed an identity reference

```bash
uv run python scripts/seed_identity_reference.py identity_002 "Presenter Name" <google_drive_url>
```

The source photo can be JPEG or PNG — it gets automatically converted and
resized (max 1024px on the long edge) before upload. **Use a tight,
front-facing headshot.** Wide shots, full-body photos, or photos where the
face is small in frame are the most common cause of D-ID's `FaceError:
face not detected` and of low `identity_similarity` scores at S30 later.

Advanced flags if you need them:
- `--max-dim 0` — disable resizing, encode at full resolution
- `--raw <path-or-url>` — skip all processing, upload an already-prepared PNG as-is

Verify it landed:

```bash
sudo docker exec temporal-postgresql psql -U temporal -d content_engine -x \
  -c "SELECT identity_id, reference_asset, reference_sample_hash FROM identity_profiles WHERE identity_id='identity_002';"
```

`reference_sample_hash` must be non-empty.

---

## Step 8 — Seed a voice reference

First, pick an ElevenLabs voice ID:

```bash
export VOICE_SYNTHESIS_API_KEY=$(grep VOICE_SYNTHESIS_API_KEY .env | cut -d= -f2)

uv run python3 -c "
from elevenlabs import ElevenLabs
import os

client = ElevenLabs(api_key=os.environ['VOICE_SYNTHESIS_API_KEY'])
for v in client.voices.get_all().voices:
    print(f\"{v.voice_id}  {v.name:20s}  {v.labels}\")
"
```

**Critical: generate a real reference sample of the voice you picked, in
that exact voice.** Do not reuse a reference sample from a different voice
ID — the S20 speaker-similarity check compares generated narration against
this file, and a mismatched reference will reliably fail with
`speaker_similarity_low` no matter how good the actual generation is.

```bash
uv run python3 -c "
from elevenlabs import ElevenLabs
import os

client = ElevenLabs(api_key=os.environ['VOICE_SYNTHESIS_API_KEY'])
audio = client.text_to_speech.convert(
    text='This is a short reference sample for speaker similarity calibration.',
    voice_id='<your_chosen_voice_id>',
    model_id='eleven_multilingual_v2',
)
with open('fixtures/stubs/voice_reference.mp3', 'wb') as f:
    for chunk in audio:
        f.write(chunk)
print('saved')
"

uv run python scripts/seed_voice_reference.py \
  voice_001 "Presenter" <your_chosen_voice_id> fixtures/stubs/voice_reference.mp3
```

**The `provider_voice_id` and the reference sample are a matched pair.**
If you ever change one, you must regenerate/reseed the other, or S20 will
fail this exact way.

---

## Step 9 — Verify both seeds

```bash
sudo docker exec temporal-postgresql psql -U temporal -d content_engine -x \
  -c "SELECT identity_id, reference_asset, reference_sample_hash FROM identity_profiles;" \
  -c "SELECT voice_id, provider_voice_id, reference_asset FROM voice_profiles;"
```

Both should show real S3 paths and non-empty hashes.

---

## Step 10 — Run the pipeline

```bash
uv run avatar-harness run \
  --config configs/runs/avatar_walking_skeleton.yaml \
  --idea "<your topic here>" \
  --run-id run_<yourname>_01 \
  --privacy private
```

**Always use a unique `--run-id` per invocation — never reuse one.**
Reusing a `run-id` across separate runs silently overwrites manifest
history for overlapping stage/attempt numbers, and Temporal will create a
second execution under the same Workflow ID, which makes debugging
genuinely confusing. `run_<yourname>_<number>` is a good convention.

If you `Ctrl+C` out of a run, terminate the workflow before running again:

```bash
temporal workflow terminate --workflow-id pipeline-run_<yourname>_01 --address localhost:7233
```

Otherwise it keeps retrying in the background using stale data the next
time a worker starts, and can silently burn real API calls.

---

## Step 11 — Confirm a clean run

```bash
sudo docker exec temporal-postgresql psql -U temporal -d content_engine \
  -c "SELECT stage_id, attempt, status FROM manifest_stage_records WHERE run_id='run_<yourname>_01' ORDER BY manifest_created_at;"
```

Every row should read `attempt=1, status=passed`, S00 through S100. Any
stage on `attempt=2` or `3` means it hit a validation failure and
self-corrected — check what it was before assuming it's fine to ignore.

---

## Troubleshooting

**`database "temporal" does not exist` / `Exited (1)` on `temporal-postgresql`**
The `sql/00-create-temporal-databases.sql` init script didn't run or isn't
present. Confirm it exists and is a real file (Step 1), then redo Step 2.

**`Is a directory` error in Postgres logs**
A file failed to download/copy correctly and left an empty directory at
that path. Check `ls -la sql/` for anything that isn't a plain file,
delete it, re-pull from git, and redo Step 2.

**`temporal-postgresql` stuck `unhealthy`, logs show repeated `FATAL: the
database system is starting up` for well over a minute**
This is normal Postgres crash-recovery after an unclean shutdown (system
sleep, killed container, etc.) — not a bug. It can take 1–3 minutes on
slow disks. Either wait it out and re-run `docker compose up -d`, or do a
fresh `down -v` rebuild to skip the recovery entirely (recommended if
you're on a fresh setup with no data worth keeping).

**D-ID error: `FaceError: face not detected`**
Almost always the source photo's framing, not a bug. Use
`scripts/compare_identity.py` to inspect the extracted video frame next to
your reference photo:
```bash
uv run python scripts/compare_identity.py --reference <path> --video <path>
```

**D-ID error: `file size exceeded 10 MB`**
Should not happen with the current `seed_identity_reference.py` (auto-
resizes). If you hit this, confirm you're not using `--raw` with an
oversized file.

**S20 fails with `speaker_similarity_low`**
The reference sample doesn't match `provider_voice_id`. Re-do Step 8,
making sure the reference sample was generated using the *same* voice ID
you seeded.

**S50 fails with `caption_timing_invalid`**
A known OpenAI Whisper quirk (degenerate zero-duration word timestamps) is
already patched in `providers/real/openai_whisper_captions.py`. If you see
this on current code, it's retryable and should self-correct within 3
attempts — if it doesn't, flag it, don't just keep retrying manually.

**Zombie/stuck workflow after a `Ctrl+C`**
See Step 10 — always terminate the workflow before starting a new run with
the same `run-id`.