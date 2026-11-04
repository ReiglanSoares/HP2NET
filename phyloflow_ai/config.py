import os


OLLAMA_API_ENDPOINT = os.getenv(
    "PHYLOFLOW_AI_OLLAMA_ENDPOINT",
    os.getenv("WORKFLOW_AI_OLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/generate"),
)
MODEL_NAME = os.getenv(
    "PHYLOFLOW_AI_MODEL",
    os.getenv("WORKFLOW_AI_MODEL", "llama3.1:8b"),
)

