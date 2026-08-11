from openai import OpenAI


def main():
    api_key = __import__("os").environ.get("OPENAI_API_KEY")

    if not api_key:
        print("ERROR: OPENAI_API_KEY is not set.")
        return

    print("Connecting to OpenAI...")

    client = OpenAI(api_key=api_key)

    model = "gpt-4.1-mini"

    print(f"Testing model: {model}")

    response = client.responses.create(
        model=model,
        input="Reply with exactly this JSON: {\"message\": \"OpenAI connection works\"}",
    )

    print("\n========== RESPONSE ==========")
    print(response.output_text)
    print("==============================")

    print("\nModel used:")
    print(response.model)


if __name__ == "__main__":
    main()