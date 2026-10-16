"""HP2NET LLM workflow planning and code-generation orchestrator."""

import argparse
import warnings

from agents import run_agent1_planner, run_agent2_test_generator
from llm import GenerationCancelled
from workflow_engine.discovery import (
    discover_implemented_workflow,
    tool_to_app_name,
)
from workflow_engine.graph import (
    build_interface_graph,
    find_path,
    resolve_workflow_path,
    validate_composition,
    validate_endpoints,
    validate_interfaces,
)
from workflow_engine.knowledge import (
    canonicalize_tool_names,
    extract_app_signatures,
    extract_available_apps,
    extract_requested_tools,
    load_apps_context,
    normalize_text,
    read_json_file,
)
from workflow_engine.patterns import IMPLEMENTATION_PATTERNS, resolve_implementation_pattern
from workflow_engine.output import save_generated_workflow
from workflow_engine.settings import (
    IMPLEMENTATION_SPEC,
    MODEL_NAME,
    OLLAMA_API_ENDPOINT,
    ORIGINAL_KB_PATH,
    TARGET_KB_PATH,
    WORKFLOW_SOURCE_PATH,
)
from workflow_engine.validation import (
    ALLOWED_WORKFLOW_GLOBALS,
    extract_python_code,
    validate_generated_code,
    validate_workflow_names,
)

TEST_KB_PATH = TARGET_KB_PATH

warnings.filterwarnings("ignore", category=SyntaxWarning)


def main(print_code=False):
    print("======================================================================")
    print("                PHYLO_AGENT 2 - HP2NET ORCHESTRATOR                   ")
    print("======================================================================\n")
    try:
        original_knowledge = read_json_file(ORIGINAL_KB_PATH)
        target_knowledge = read_json_file(TARGET_KB_PATH)
    except Exception as error:
        print(f"[ERROR] Failed to load knowledge base files: {error}")
        return

    user_request = input("Enter workflow request: ").strip()
    if not user_request:
        user_request = "Use BUCKy to perform concordance analysis and then use SNaQ to infer a phylogenetic network."
        print(f"[INFO] Using default request: '{user_request}'\n")

    requested_tools = extract_requested_tools(user_request, original_knowledge)
    print(f"[0/3] Extracted requested tools: {requested_tools}")
    print("[1/3] Running Agent 1 (Planner over original HP2Net KB)...")
    agent1_output = run_agent1_planner(user_request, original_knowledge, requested_tools)
    proposed_steps = canonicalize_tool_names(agent1_output.get("steps", []), original_knowledge)
    goal = agent1_output.get("goal", user_request)
    print(f"      -> Goal: {goal}")
    print(f"      -> Proposed steps: {proposed_steps}\n")
    if not proposed_steps:
        print("[ERROR] Agent 1 did not generate a valid step sequence.")
        return

    known_tools = set(original_knowledge.get("tools", {})) | set(target_knowledge.get("tools", {}))
    unknown_tools = [tool for tool in proposed_steps if tool not in known_tools]
    if unknown_tools:
        print("[REJECTED BY PYTHON VALIDATOR] Unknown tool(s) proposed: " + ", ".join(unknown_tools))
        print("[3/3] Agent 2 skipped.")
        return

    graph_resolved_steps = resolve_workflow_path(requested_tools, target_knowledge)
    if graph_resolved_steps:
        if proposed_steps != graph_resolved_steps:
            print("[INFO] Agent 1 proposal differs from deterministic graph resolution.")
            print(f"      Agent 1: {proposed_steps}")
            print(f"      Graph:   {graph_resolved_steps}")
        proposed_steps = graph_resolved_steps
        print(f"[1.5/3] Graph path finding resolved steps: {proposed_steps}")
    else:
        print("[1.5/3] No valid path found in target KB graph.")
        proposed_steps = requested_tools

    print("[2/3] Validating plan against Target KB Graph...")
    validation = validate_composition(proposed_steps, target_knowledge)
    validated_plan = {
        "goal": goal,
        "workflow_name": "-".join(proposed_steps),
        "steps": proposed_steps,
        "status": validation.get("status"),
        "execution_status": validation.get("execution_status"),
        "implementation_notes": validation.get("implementation_notes")
    }
    print(f"      -> Validation status: {validated_plan['status']} ({validated_plan['execution_status']})")
    print(f"      -> Notes: {validated_plan['implementation_notes']}\n")

    status = validated_plan["status"]
    if status == "existing_workflow":
        print("[3/3] Agent 2 skipped.")
        print(f"      -> Workflow already exists and is implemented in the target KB: {validated_plan['implementation_notes']}")
        return
    if status != "new_supported_workflow":
        print("[3/3] Agent 2 skipped.")
        print("      -> Composition is not supported by the target KB graph.")
        return

    discovered_workflow = discover_implemented_workflow(proposed_steps, target_knowledge)
    if discovered_workflow:
        print(
            "[2.5/3] Existing implementation discovered in "
            f"{discovered_workflow['source']}: {discovered_workflow['function_name']}"
        )
        print(f"      -> Scientific steps: {discovered_workflow['steps']}")
        print(f"      -> apps.* calls: {discovered_workflow['apps']}\n")
        print("[3/3] Agent 2 skipped.")
        print("      -> The requested workflow already exists in the source code.")
        return

    pattern = resolve_implementation_pattern(proposed_steps)
    if pattern:
        print(f"[2.5/3] Resolved HP2Net implementation pattern: {pattern['apps']}\n")

    print("[3/3] Running Agent 2 (Parsl Code Generator)...")
    generated_code = run_agent2_test_generator(
        validated_plan,
        pattern,
        IMPLEMENTATION_SPEC,
        load_apps_context()
    )
    all_passed, checks, error_message = validate_generated_code(
        generated_code,
        proposed_steps,
        pattern
    )
    print("Code Validation:")
    for check_name, check_status in checks.items():
        print(f"  {check_name}: {check_status}")
    print()
    if not all_passed:
        print(f"[ERROR] Code validation failed. Reason: {error_message if error_message else 'Semantic checks failed.'}")
        return

    if print_code:
        print("======================================================================")
        print("CODE GENERATED BY AGENT 2:")
        print("======================================================================")
        print(generated_code)
        print("======================================================================")
        return

    code_path, metadata_path = save_generated_workflow(
        generated_code,
        validated_plan,
        MODEL_NAME,
        checks,
        pattern
    )
    print("Generated workflow saved successfully:")
    print(f"  Code: {code_path}")
    print(f"  Metadata: {metadata_path}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Plan, validate, and generate HP2NET Parsl workflows."
    )
    parser.add_argument(
        "--print-code",
        action="store_true",
        help="Print validated code without saving files."
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        arguments = parse_arguments()
        main(print_code=arguments.print_code)
    except GenerationCancelled:
        print("Execução encerrada sem gerar ou alterar arquivos.")
    except KeyboardInterrupt:
        print("\nSolicitação cancelada pelo usuário.")
