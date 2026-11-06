import os

from agent_helpers.llm_client import ask_llm_model, get_model_client


OLLAMA_HOST = "http://localhost:11434"
DEFAULT_MODEL = os.getenv("RESEARCHER_MODEL", os.getenv("VERTEX_DEFAULT_MODEL", "llama3.1"))
researcher = get_model_client(OLLAMA_HOST)


if __name__ == "__main__":
    reply = ask_llm_model(
        researcher,
        model=DEFAULT_MODEL,
        system_prompt="You are a stock researcher.",
        user_prompt="What should I investigate about AAPL before a deeper analysis?",
        host_label=OLLAMA_HOST,
    )

    print(reply)
