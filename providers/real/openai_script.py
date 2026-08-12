from __future__ import annotations

import json

from openai import OpenAI

from contracts.common.envelope import StageEnvelopeV1, StageOutputV1
from contracts.prompts.script_generation_prompt import (
    SCRIPT_GENERATION_SYSTEM_PROMPT,
    build_script_prompt,
)
from contracts.stages.s10_script import ScriptPackageV1
from orchestrator.provider_config import load_provider_config
from orchestrator.storage import get_artifact, put_artifact


class OpenAIScriptProvider:
    """OpenAI-backed provider for S10 script generation."""

    capability: str = "script_generation"

    def __init__(self):
        config = load_provider_config("script_generation")

        api_key = config.get("api_key")
        if not api_key:
            raise ValueError(
                "OpenAI API key is missing from "
                "configs/providers/script_generation.yaml "
                "or the environment."
            )

        self._model_name = config.get("model_id", "gpt-4.1-mini")
        self._client = OpenAI(api_key=api_key)

    def run(
        self,
        envelope: StageEnvelopeV1,
        run_id: str,
    ) -> StageOutputV1:
        # S10 consumes the idea artifact produced by the previous stage.
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

        topic = idea_data.get("topic")

        if not isinstance(topic, str) or not topic.strip():
            raise ValueError(
                "Idea artifact is missing a valid non-empty topic."
            )

        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=[
                    {
                        "role": "system",
                        "content": SCRIPT_GENERATION_SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": build_script_prompt(topic),
                    },
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise RuntimeError(
                f"OpenAI script generation request failed "
                f"using model '{self._model_name}'."
            ) from exc

        if not response.choices:
            raise RuntimeError(
                "OpenAI returned no choices for script generation."
            )

        message = response.choices[0].message
        raw = message.content

        if not raw or not raw.strip():
            raise ValueError(
                "OpenAI returned an empty script-generation response."
            )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "OpenAI returned invalid JSON for script generation."
            ) from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                "OpenAI script-generation response must be a JSON object."
            )

        scenes = parsed.get("scenes")

        if not isinstance(scenes, list):
            raise ValueError(
                "OpenAI script-generation response is missing "
                "the required 'scenes' list."
            )

        try:
            script = ScriptPackageV1(
                run_id=run_id,
                scenes=scenes,
            )
        except Exception as exc:
            raise ValueError(
                "OpenAI response does not match ScriptPackageV1."
            ) from exc

        script_bytes = json.dumps(
            script.model_dump()
        ).encode("utf-8")

        try:
            artifact = put_artifact(
                data=script_bytes,
                artifact_id=f"script_{run_id}",
                mime_type="application/json",
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to store generated script artifact "
                f"for run '{run_id}'."
            ) from exc

        return StageOutputV1(
            payload=script.model_dump(),
            metadata={
                "provider": "openai_script",
                "model": self._model_name,
                "scene_count": len(script.scenes),
            },
            artifact_refs=[artifact],
        )