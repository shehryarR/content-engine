Contract drift classification vs m0-contract-freeze
================================================================

Changed paths under contracts/, graph/: 14

NEW SCHEMA FILES (3) — additive by construction:
  + contracts/common/v1/correction_plan.schema.json
  + contracts/common/v1/stage_run_record.schema.json
  + contracts/common/v1/validation_failure.schema.json

NON-SCHEMA SOURCES (9) — schema diff is authoritative:
  . contracts/common/correction_plan.py (new)
  . contracts/common/envelope.py
  . contracts/common/telemetry.py (new)
  . contracts/common/validation_failure.py (new)
  . contracts/prompts/__init__.py (new)
  . contracts/prompts/script_generation_prompt.py (new)
  . contracts/stages/s70_qc.py
  . graph/__init__.py (new)
  . graph/pipeline_graph.py (new)

ADDITIVE DELTAS (5):
  ~ contracts/common/v1/validation_report.schema.json: field added -> failed_field (optional)
  ~ contracts/common/v1/validation_report.schema.json: field added -> failure_type (optional)
  ~ contracts/stages/v1/quality_report.schema.json: field added -> subjective_quality_confidence (optional)
  ~ contracts/stages/v1/quality_report.schema.json: field added -> subjective_quality_passed (optional)
  ~ contracts/stages/v1/quality_report.schema.json: field added -> subjective_quality_rationale (optional)

BREAKING DELTAS (0): none

================================================================
VERDICT: ADDITIVE_ONLY — no field removed, none made required,
         no type narrowed, no enum value removed. v1 readers of
         M0-era documents still validate. Freeze intact in substance.