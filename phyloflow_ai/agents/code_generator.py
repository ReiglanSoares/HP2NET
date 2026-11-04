import json

from ..llm import GenerationCancelled, generate_stream
from ..config import MODEL_NAME, OLLAMA_API_ENDPOINT
from ..engine.validation import extract_python_code


def run_agent2_test_generator(validated_plan, pattern, spec, implementation_context):
    project_name = spec.get("project_name", "the target project")
    steps = validated_plan.get("steps", [])
    steps_str = " -> ".join(steps)
    workflow_name = validated_plan.get("workflow_name", "custom_workflow").lower().replace("-", "_")
    pattern_instruction = ""
    if pattern:
        pattern_instruction = (
            f"REQUIRED {project_name} IMPLEMENTATION PATTERN:\n{pattern.get('description', '')}\n\n"
            f"LIST OF PROJECT APPS TO CALL IN ORDER:\n{json.dumps(pattern.get('apps', []), indent=2)}\n\n"
            "Do not substitute or bypass this pattern with direct calls between scientific tools."
        )
    few_shot_example = spec.get("few_shot_example", '''
FEW-SHOT EXAMPLE OF EXPECTED OUTPUT:

def raxml_astral(basedir, config, prepare_to_run):
    datalist = glob.glob(
        os.path.join(basedir["dir"], "input", "phylip", "*.phy")
    )

    ret_tree = []

    for input_file in datalist:
        ret_tree.append(
            apps.raxml(
                basedir=basedir,
                config=config,
                inputs=prepare_to_run,
                input_file=input_file
            )
        )

    ret_sad = apps.setup_tree_output(
        basedir=basedir,
        config=config,
        inputs=ret_tree
    )

    ret_ast = apps.astral(
        basedir=basedir,
        config=config,
        inputs=[ret_sad]
    )

    return ret_ast
''')
    system_prompt = (
        f"You are Agent 2: Test & Code Implementation Agent for {project_name}.\n"
        "Your task is ONLY to generate the single Python function for the requested steps and required implementation pattern.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. DO NOT output existing functions like raxml_snaq or iqtree_snaq.\n"
        "2. Implement ONLY the target steps chain and its associated project implementation pattern.\n"
        "3. Output ONLY valid executable Python code without markdown blocks or text.\n"
        "4. Pay strict attention to required positional and keyword parameters in AVAILABLE APPS CONTRACTS.\n"
        "5. Do not invent configuration attributes or file paths. Use only configuration fields present in bioconfig.py and filesystem paths established by the implementation pattern.\n"
        "6. Do not emit optional arguments unless the implementation pattern or target workflow requires them.\n\n"
        f"{few_shot_example}"
    )
    user_prompt = (
        f"SCIENTIFIC WORKFLOW STEPS: {steps_str}\n"
        f"FUNCTION NAME: {workflow_name}\n\n"
        f"{pattern_instruction}\n\n"
        f"SPECIFICATION RULES:\n{json.dumps(spec.get('rules', []), indent=2)}\n\n"
        f"AVAILABLE APPS CONTRACTS:\n{implementation_context}\n\n"
        f"Generate Python function {workflow_name}:"
    )
    payload = {
        "model": MODEL_NAME,
        "prompt": f"System: {system_prompt}\nUser: {user_prompt}",
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 8192, "num_predict": 1024}
    }
    try:
        raw_content = generate_stream(OLLAMA_API_ENDPOINT, payload, label="Agent 2").strip()
        return extract_python_code(raw_content)
    except GenerationCancelled:
        raise
    except Exception as error:
        return f"# AGENT 2 ERROR: Failed to generate Python code: {error}"
