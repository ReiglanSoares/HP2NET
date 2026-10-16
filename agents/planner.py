import json

from llm import GenerationCancelled, generate_stream
from workflow_engine.settings import MODEL_NAME, OLLAMA_API_ENDPOINT


def run_agent1_planner(user_prompt, original_knowledge, requested_tools):
    system_prompt = (
        "You are an expert computational biology workflow planner for HP2Net.\n"
        "Translate the user's request into an ordered sequence of HP2Net tools.\n\n"
        f"EXPLICITLY REQUESTED TOOLS: {json.dumps(requested_tools)}\n\n"
        "HARD CONSTRAINTS:\n"
        "1. Preserve the first and last tools explicitly requested by the user.\n"
        "2. If the requested endpoints are not directly connected, find a valid path between them using the original HP2Net knowledge base.\n"
        "3. Insert ONLY intermediate tools that are required by a valid interface in the original KB graph.\n"
        "4. Do NOT replace either requested endpoint.\n"
        "5. Do NOT add tools before the first requested tool or after the last requested tool.\n"
        "6. If EXPLICITLY REQUESTED TOOLS has only 1 item, output EXACTLY that 1 item.\n"
        "7. If no valid path connects the endpoints in the original KB, output only the requested tools as steps.\n\n"
        "EXAMPLE:\n"
        "If the user requests BUCKy and SNaQ (endpoints: ['BUCKy', 'SNaQ']) and the original KB defines\n"
        "the chain BUCKy -> Quartet MaxCut -> SNaQ, output:\n"
        "[\"BUCKy\", \"Quartet MaxCut\", \"SNaQ\"]\n\n"
        f"Available original HP2Net knowledge:\n{json.dumps(original_knowledge, indent=2)}\n\n"
        "Output JSON with keys 'goal' (string) and 'steps' (list of strings)."
    )
    payload = {
        "model": MODEL_NAME,
        "prompt": f"System: {system_prompt}\nUser request: {user_prompt}",
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 512}
    }
    try:
        raw_text = generate_stream(OLLAMA_API_ENDPOINT, payload, label="Agent 1").strip()
        return json.loads(raw_text)
    except GenerationCancelled:
        raise
    except Exception as error:
        print(f"[AGENT 1 ERROR] Communication or parsing failure: {error}")
        return {"goal": "", "steps": []}
