from __future__ import annotations

import json

from contracts.common.envelope import (
    ProviderDescriptorV1,
    StageEnvelopeV1,
)
from contracts.stages.s10_script import ScriptPackageV1
from orchestrator.storage import put_artifact
from providers.openai_script import OpenAIScriptProvider


def main():
    run_id = "smoke_openai_script"

    topic = "The history and cultural significance of coffee"

    print("Preparing test idea artifact...")
    print(f"Topic: {topic}")

    idea_data = {
        "run_id": run_id,
        "topic": topic,
    }

    idea_ref = put_artifact(
        data=json.dumps(idea_data).encode("utf-8"),
        artifact_id="idea_smoke_openai",
        mime_type="application/json",
    )

    print(f"Idea artifact: {idea_ref.path}")
    print(f"Idea hash: {idea_ref.hash}")

    # Build the same type of envelope S10 receives in the pipeline.
    envelope = StageEnvelopeV1(
        stage_id="S10",
        attempt=1,
        input_hash="a" * 64,
        artifact_refs=[idea_ref],
        provider=ProviderDescriptorV1(
            provider="openai",
            model="gpt-4.1-mini",
            version="1.0.0",
            capability="script_generation",
        ),
    )

    print("\nInitializing OpenAI script provider...")
    provider = OpenAIScriptProvider()

    print("Generating script with OpenAI...")
    print("This will call the API, validate ScriptPackageV1,")
    print("and store the generated script in MinIO.\n")

    output = provider.run(
        envelope=envelope,
        run_id=run_id,
    )

    # Validate the provider output once more at the smoke-test boundary.
    script = ScriptPackageV1.model_validate(output.payload)

    if not script.scenes:
        raise AssertionError(
            "OpenAI provider returned an empty scenes list."
        )

    if not output.artifact_refs:
        raise AssertionError(
            "OpenAI provider returned no script artifact."
        )

    print("\n========== SUCCESS ==========")
    print("OpenAI script generation completed.")
    print(f"Provider: {provider.capability}")
    print(f"Number of scenes: {len(script.scenes)}")
    print(f"Metadata: {output.metadata}")

    print("\nGenerated script artifact:")
    for artifact in output.artifact_refs:
        print(f"  artifact_id: {artifact.artifact_id}")
        print(f"  path:        {artifact.path}")
        print(f"  hash:        {artifact.hash}")
        print(f"  mime_type:   {artifact.mime_type}")

    print("\nGenerated scenes:")
    for index, scene in enumerate(script.scenes, start=1):
        print(f"\nScene {index}:")
        print(scene)

    print("\n✓ OpenAI script provider smoke test passed.")


if __name__ == "__main__":
    main()