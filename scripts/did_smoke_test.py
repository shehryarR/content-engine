import os
import time
from pathlib import Path

import requests


DID_API_URL = "https://api.d-id.com"

IMAGE_PATH = Path("fixtures/stubs/mona_lisa.png")
AUDIO_PATH = Path(
    "fixtures/stubs/voice_run_20260810_151321_851f694f20e75967989ad4eb580b443f15f0020dbafa12d370f4135bed639365.mp3"
)


def main():
    api_key = os.getenv("DID_API_KEY")

    if not api_key:
        print("Error: DID_API_KEY environment variable is not set.")
        return

    if not IMAGE_PATH.exists():
        print(f"Error: Image not found: {IMAGE_PATH}")
        return

    if not AUDIO_PATH.exists():
        print(f"Error: Audio not found: {AUDIO_PATH}")
        return

    headers = {
        "Authorization": f"Basic {api_key}",
    }

    print("Uploading image to D-ID...")
    with open(IMAGE_PATH, "rb") as image_file:
        response = requests.post(
            f"{DID_API_URL}/images",
            headers=headers,
            files={"image": ("mona_lisa.png", image_file, "image/png")},
        )

    response.raise_for_status()
    image_data = response.json()
    image_url = image_data["url"]

    print(f"Image uploaded: {image_url}")

    print("Uploading audio to D-ID...")
    with open(AUDIO_PATH, "rb") as audio_file:
        response = requests.post(
            f"{DID_API_URL}/audios",
            headers=headers,
            files={"audio": ("voice.mp3", audio_file, "audio/mpeg")},
        )

    response.raise_for_status()
    audio_data = response.json()
    audio_url = audio_data["url"]

    print(f"Audio uploaded: {audio_url}")

    print("Creating D-ID talk...")
    response = requests.post(
        f"{DID_API_URL}/talks",
        headers={
            **headers,
            "Content-Type": "application/json",
        },
        json={
            "source_url": image_url,
            "script": {
                "type": "audio",
                "audio_url": audio_url,
            },
        },
    )

    response.raise_for_status()
    talk_data = response.json()
    talk_id = talk_data["id"]

    print(f"Talk created: {talk_id}")

    print("Waiting for video generation...")

    while True:
        response = requests.get(
            f"{DID_API_URL}/talks/{talk_id}",
            headers=headers,
        )
        response.raise_for_status()

        status_data = response.json()
        status = status_data.get("status")

        print(f"Status: {status}")

        if status == "done":
            result_url = status_data["result_url"]
            break

        if status == "error":
            raise RuntimeError(
                f"D-ID generation failed: {status_data}"
            )

        time.sleep(5)

    print("Downloading generated video...")

    output_path = Path("fixtures/stubs/did_smoke_test.mp4")

    response = requests.get(result_url)
    response.raise_for_status()

    output_path.write_bytes(response.content)

    print(f"Success! Video saved to: {output_path}")
    print(f"Video size: {len(response.content)} bytes")


if __name__ == "__main__":
    main()