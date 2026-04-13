from pathlib import Path
import sys


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from _shared import ask_ollama_model, get_ollama_client


OLLAMA_HOST = "http://localhost:11434"
researcher = get_ollama_client(OLLAMA_HOST)


reply = ask_ollama_model(
    researcher,
    model="llama3.1",
    system_prompt="You are a stock researcher.",
    user_prompt="What should I investigate about AAPL before a deeper analysis?",
    host_label=OLLAMA_HOST,
)

print(reply)
