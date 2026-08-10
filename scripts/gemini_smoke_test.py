import asyncio
import json
from google import genai
from pydantic import ValidationError
from orchestrator.provider_config import load_provider_config
from contracts.prompts.script_generation_prompt import SCRIPT_GENERATION_SYSTEM_PROMPT, build_script_prompt
from contracts.stages.s10_script import ScriptPackageV1


def main():
    print("Loading config...")
    config = load_provider_config('script_generation')

    if not config.get('api_key'):
        print("Error: No API key found in configs/providers/script_generation.yaml")
        return
    print("Configuring Gemini...")
    client = genai.Client(api_key=config['api_key'])

    topic = "The history of coffee"
    print(f"Generating script for topic: '{topic}'...")

    response = client.models.generate_content(
        model=config.get('model_id', 'gemini-1.5-flash'),
        contents=build_script_prompt(topic),
        config={
            "system_instruction": SCRIPT_GENERATION_SYSTEM_PROMPT,
        },
    )

    raw = response.text.strip()
    print("\n--- Raw Response from Gemini ---")
    print(raw)
    print("--------------------------------\n")

    try:
        parsed = json.loads(raw)
        script = ScriptPackageV1(run_id="smoke_test",scenes=parsed["scenes"],)
        print("Success! The response is valid JSON and matches ScriptPackageV1.")
        print(f"Number of scenes generated: {len(script.scenes)}")
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"Error: Invalid ScriptPackageV1 response: {e}")


if __name__ == "__main__":
    main()

