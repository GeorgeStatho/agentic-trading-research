from pathlib import Path
import os
import sys


AGENT_CALLERS_DIR = Path(__file__).resolve().parent
if str(AGENT_CALLERS_DIR) not in sys.path:
    sys.path.append(str(AGENT_CALLERS_DIR))

from _shared import ask_llm_model, get_model_client


OLLAMA_HOST = "http://localhost:11434"
DEFAULT_MODEL = os.getenv("RESEARCHER_MODEL", os.getenv("VERTEX_DEFAULT_MODEL", "llama3.1"))
researcher = get_model_client(OLLAMA_HOST)


reply = ask_llm_model(
    researcher,
    model=DEFAULT_MODEL,
    system_prompt="You are a stock researcher.",
    user_prompt="What should I investigate about AAPL before a deeper analysis?",
    host_label=OLLAMA_HOST,
)

print(reply)
