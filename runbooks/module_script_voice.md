# Fatima's Module Runbook: Script (S10) & Voice (S20)

This runbook covers the architecture, configuration, operation, error handling, fixtures, and testing instructions for Fatima's surface area of the content pipeline: **S10 Script Generation** and **S20 Voice Synthesis**.

---

## 1. S10 Script Generation

S10 takes an approved idea request (from S00/Intake) and generates a structured screenplay package matching the specified topic and modality.

### Provider Capabilities
- **Capability Key:** `script_generation`
- **Supported Providers:** 
  - `openai_script` (backed by OpenAI Chat Completions API)
  - `gemini_script` (backed by Google GenAI SDK Client API)
  - `stub_script_provider` (backed by a deterministic, offline stub)

### Configuration Location
The active provider config lives at `configs/providers/script_generation.yaml`. 
An example configuration file is available at `configs/providers/script_generation.yaml.example`.

Example structure:
```yaml
api_key: "sk-proj-..."
model_id: "gpt-4.1-mini"  # or gemini-1.5-flash for Gemini
```

### Exit Validators & Constraints
S10 outputs must validate against `_validate_s10` in `orchestrator/stage_executor.py`:
1. **Scene Count:** The generated script package must contain exactly **3 scenes**.
2. **Types:** The payload must contain exactly `run_id` (string) and `scenes` (list of non-empty strings).
3. **Combined Length:** Sum of all characters in the three scenes must be **<= 1500 characters** (to fit within downstream synthesis and D-ID video constraints).

### Error Codes and Recovery
- `malformed_script`: Raised when scene count is not 3, scenes are missing, or empty.
  - *Recovery:* Check downstream prompt context or re-run the stage with a shorter topic.
- `hash_mismatch`: Raised if the generated script package fails SHA-256 integrity check upon upload.
  - *Recovery:* Verify storage (S3/MinIO) connectivity and bucket read/write permissions.

### How to Re-run S10
You can invoke the pipeline CLI to execute script generation for a run:
```bash
uv run python cli.py run-stage --stage S10 --run-id <run-id>
```

---

## 2. S20 Voice Synthesis

S20 consumes the script scenes from S10 and synthesizes the narration audio file using ElevenLabs text-to-speech engine.

### Provider Capabilities
- **Capability Key:** `voice_synthesis`
- **Supported Providers:**
  - `elevenlabs` (backed by ElevenLabs Python Client SDK)
  - `stub_voice` (backed by deterministic silent WAV fixture file)

### Configuration Location
Configured via `configs/providers/voice_synthesis.yaml` (see `configs/providers/voice_synthesis.yaml.example` for template).

### ElevenLabs & Voice Registry Setup
Voice IDs must be pre-registered and approved in the database registry under `voice_profiles` with `consent_status = 'active'`.

#### Active Voice Registry Query
```sql
SELECT voice_id, provider_voice_id, consent_status FROM voice_profiles;
```

#### voice_002 Setup
The reference voice sample for `voice_002` (used for identity verification and similarity checking) must be seeded in the storage registry using `scripts/seed_identity_reference.py` or stored as `fixtures/stubs/ref_accepted_v1.wav`.

### Speaker Similarity Gating
To prevent deepfake spoofing and guarantee quality control, S20 performs a FFT-based speaker similarity check between the freshly generated MP3/WAV narration and the reference voice sample.
- **Threshold:** `0.72` (calibrated midpoint).
- **Metric:** Cosine similarity computed on the mean power spectra (using NumPy FFT) of both clips extracted as mono 16kHz PCM.
- **Error Condition:** If similarity falls below the threshold, the validator reports `speaker_similarity_low` and fails the stage.

---

## 3. Fixtures Management

All test and runtime fixtures are stored in the `fixtures/` directory.

### Catalog Structure
- `fixtures/valid/`: Positive test cases (e.g., `idea_request_faceless.json`, `script_package.json`, `voice_track.json`).
- `fixtures/failures/`: Negative validation fixtures (e.g., `malformed_script.json`, `corrupt_audio.wav`, `disclosure_faceless_false.json`).
- `fixtures/stubs/`: Audio/video stub binaries (e.g., `silent_5s.wav`, `black_5s.mp4`, speaker verification references `ref_accepted_v1.wav`/`gen_accepted_v1.wav`).

### Face-specific and Modality-neutral Fields
When adding new fixtures:
- `modality` must be `"avatar"` or `"faceless"`.
- `"identity_id"` is **required** if modality is `"avatar"` but must be omitted (or skipped) if modality is `"faceless"`.
- `"contains_synthetic_media"` must be `true` for all published outputs.

---

## 4. Tests and Verification

We maintain a robust validation suite to verify S10/S20 without calling external paid APIs.

### 1. Validator Dry Run Suite
Exercises the exit validators (including script structural validation and voice similarity checks) using mocked payloads.
```bash
uv run pytest tests/test_validator_dry_run.py -v
```

### 2. Failure Injection Suite
Simulates transient network errors, malformed responses, and incorrect hashes, ensuring the orchestrator retries or fails gracefully.
```bash
uv run pytest tests/integration/test_failure_injection.py -v
```

### 3. Fatima Stub Providers Suite
Validates that the offline stub providers correctly produce artifacts that pass downstream stages.
```bash
uv run pytest tests/test_stub_providers_fatima.py -v
```
