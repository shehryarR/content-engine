from __future__ import annotations

from contracts.common.envelope import (
    ArtifactRefV1,
    ProviderDescriptorV1,
    StageEnvelopeV1,
)
from orchestrator.storage import put_artifact
from providers.did_avatar import DIDAvatarProvider


def main():
    run_id = "smoke_did_avatar"

    image_path = "fixtures/stubs/mona_lisa.png"
    audio_path = (
        "fixtures/stubs/"
        "voice_run_20260810_151321_"
        "851f694f20e75967989ad4eb580b443f15f0020dbafa12d370f4135bed639365.mp3"
    )

    print("Loading test files...")

    with open(image_path, "rb") as f:
        image_data = f.read()

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    print(f"Image: {len(image_data):,} bytes")
    print(f"Audio: {len(audio_data):,} bytes")

    # Store the local test files in MinIO so the provider receives
    # real ArtifactRefV1 objects, just like it will in the pipeline.
    print("\nUploading test artifacts to MinIO...")

    identity_ref = put_artifact(
        data=image_data,
        artifact_id="identity_ref_smoke_test",
        mime_type="image/png",
    )

    audio_ref = put_artifact(
        data=audio_data,
        artifact_id="voice_smoke_test",
        mime_type="audio/mpeg",
    )

    print(f"Identity artifact: {identity_ref.path}")
    print(f"Identity hash: {identity_ref.hash}")
    print(f"Audio artifact: {audio_ref.path}")
    print(f"Audio hash: {audio_ref.hash}")

    # Build the same kind of envelope S30 will receive.
    envelope = StageEnvelopeV1(
        stage_id="S30",
        attempt=1,
        input_hash="a" * 64,
        artifact_refs=[
            identity_ref,
            audio_ref,
        ],
        provider=ProviderDescriptorV1(
            provider="did",
            model="talks",
            version="1.0.0",
            capability="avatar_render",
        ),
    )

    print("\nInitializing D-ID provider...")
    provider = DIDAvatarProvider()

    print("Calling D-ID avatar provider...")
    print("This will upload the image/audio, create a talk,")
    print("wait for completion, download the MP4, and store it in MinIO.\n")

    output = provider.run(
        envelope=envelope,
        run_id=run_id,
    )

    print("\n========== SUCCESS ==========")
    print("D-ID avatar generation completed.")
    print(f"Provider: {provider.capability}")
    print(f"Output payload: {output.payload}")
    print(f"Metadata: {output.metadata}")

    print("\nGenerated artifact:")
    for artifact in output.artifact_refs:
        print(f"  artifact_id: {artifact.artifact_id}")
        print(f"  path:        {artifact.path}")
        print(f"  hash:        {artifact.hash}")
        print(f"  mime_type:   {artifact.mime_type}")

    print("\n✓ D-ID provider smoke test passed.")


if __name__ == "__main__":
    main()