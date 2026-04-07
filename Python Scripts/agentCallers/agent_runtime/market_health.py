from ollama import Client

OLLAMA_HOST = "http://localhost:11434"
researcher = Client(host=OLLAMA_HOST)

def ask_model(client: Client, model: str, system_prompt: str, user_prompt: str) -> str:
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response["message"]["content"]

reply = ask_model(
    researcher,
    model="llama3.1",
    system_prompt="You are a stock researcher.",
    user_prompt="What should I investigate about AAPL before a deeper analysis?",
)

print(reply)