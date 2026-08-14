import json

from google import genai

from pydantic import ValidationError
from contracts.stages.idea_request import IdeaRequestV1
from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.prompts.script_generation_prompt import (
    SCRIPT_GENERATION_SYSTEM_PROMPT,
    build_script_prompt,
)
from contracts.stages.s10_script import ScriptPackageV1
from orchestrator.provider_config import load_provider_config
from orchestrator.storage import get_artifact, put_artifact


class GeminiScriptProvider:
    capability: str = "script_generation"

    def __init__(self):
        config = load_provider_config("script_generation")

        self._model_name = config.get("model_id", "gemini-1.5-flash")

        self._client = genai.Client(api_key=config["api_key"])

    def run(self, envelope: StageEnvelopeV1, run_id: str) -> StageOutputV1:

        idea_ref = next(
            (
                ref
                for ref in envelope.artifact_refs
                if "idea" in ref.artifact_id
            ),
            None,
        )

        if idea_ref is None:
            raise ValueError(
                "S10 requires an idea artifact in envelope.artifact_refs."
            )

        try:
            idea_data = json.loads(get_artifact(idea_ref))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Idea artifact {idea_ref.artifact_id} is not valid JSON."
            ) from exc

        try:
            idea_request = IdeaRequestV1.model_validate(idea_data)
        except ValidationError as exc:
            raise ValueError(
                f"Idea artifact {idea_ref.artifact_id} "
                "is not a valid IdeaRequestV1."
            ) from exc

        if not idea_request.topic.strip():
            raise ValueError(
                "S10 requires a non-empty topic."
            )

        if not idea_request.voice_id.strip():
            raise ValueError(
                "S10 requires a non-empty voice_id."
            )

        topic = idea_request.topic

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=build_script_prompt(topic),
            config={
                "system_instruction": SCRIPT_GENERATION_SYSTEM_PROMPT,
            },
        )

        raw = response.text.strip()
        parsed = json.loads(raw)

        if "scenes" not in parsed:
            raise ValueError(
                "Gemini response missing 'scenes' field."
            )

        script = ScriptPackageV1(
            run_id=run_id,
            scenes=parsed["scenes"],
        )

        script_bytes = json.dumps(
            script.model_dump()
        ).encode("utf-8")

        artifact = put_artifact(
            data=script_bytes,
            artifact_id=f"script_{run_id}",
            mime_type="application/json",
        )

        return StageOutputV1(
            payload=script.model_dump(),
            metadata={
                "provider": "gemini_script",
                "model": self._model_name,
                "scene_count": len(script.scenes),
            },
            artifact_refs=[artifact],
        )