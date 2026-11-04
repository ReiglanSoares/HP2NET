import json

from ..config import MODEL_NAME, OLLAMA_API_ENDPOINT
from ..llm import generate_stream


def analyze_knowledge_gaps(diagnostics, source_context):
    prompt = (
        "You are reviewing candidate additions to a phylogenetic workflow knowledge base.\n"
        "Use only the supplied diagnostics and scientific source context.\n"
        "Do not claim that a documented capability is implemented. Do not invent tools, "
        "artifacts, conversions, or citations. Distinguish a missing preceding input, a "
        "possible branch, and a missing adapter implementation.\n"
        "Return JSON only, with this shape: "
        "{\"analyses\":[{\"diagnostic_id\":string,\"interpretation\":string,"
        "\"recommended_action\":string,\"confidence\":\"high|medium|low\","
        "\"assumptions\":[string],\"source_ids\":[string]}]}.\n\n"
        f"SELECTED SCIENTIFIC CONTEXT:\n{json.dumps(source_context, indent=2)}\n\n"
        f"DIAGNOSTICS TO ANALYZE:\n{json.dumps(diagnostics, indent=2)}\n\n"
        "FINAL REQUIREMENT: return one concise analyses item for every diagnostic_id above. "
        "Discuss the missing artifact and candidate producers, not the source registry itself. "
        "Use at most 45 words for interpretation and 45 words for recommended_action. "
        "Cite source_ids only when that exact source supports the artifact claim."
    )
    analysis_schema = {
        "type": "object",
        "properties": {
            "analyses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "diagnostic_id": {"type": "string"},
                        "interpretation": {"type": "string"},
                        "recommended_action": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "assumptions": {"type": "array", "items": {"type": "string"}},
                        "source_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "diagnostic_id", "interpretation", "recommended_action",
                        "confidence", "assumptions", "source_ids"
                    ],
                },
            }
        },
        "required": ["analyses"],
    }
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "format": analysis_schema,
        "options": {"temperature": 0.1, "num_predict": 420},
    }
    raw = generate_stream(
        OLLAMA_API_ENDPOINT,
        payload,
        label="Knowledge suggestions",
    ).strip()
    result = json.loads(raw)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("analyses", "suggestions", "analysis"):
            value = result.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return [value]
        if result.get("diagnostic_id"):
            return [result]
    raise ValueError(
        "The LLM did not return a recognized analysis structure. "
        f"Response preview: {raw[:300]}"
    )
