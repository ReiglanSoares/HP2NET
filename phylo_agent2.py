"""HP2NET LLM workflow planning and code-generation orchestrator."""

import argparse
import json
from pathlib import Path
import warnings

from agents import run_agent1_planner, run_agent2_test_generator
from llm import GenerationCancelled
from workflow_engine.artifacts import ArtifactError, run_artifact_command
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
from workflow_engine.integration import IntegrationError, build_integration_preview
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
from workflow_engine.output import (
    create_preview_workspace,
    load_preview_workspace,
    save_generated_workflow,
    update_integration_preview,
)
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


def _print_validation_checks(checks):
    print("Code Validation:")
    for check_name, check_status in checks.items():
        print(f"  {check_name}: {check_status}")
    print()


def _create_preview(
    generated_code,
    validated_plan,
    checks,
    workspace,
    pattern=None,
    generated_source=None
):
    integration_result = build_integration_preview(
        generated_code,
        workspace["workflow"],
        baseline_workflow_path=workspace["source"]
    )
    workflow_path, diff_path, metadata_path = update_integration_preview(
        workspace,
        integration_result,
        validated_plan,
        MODEL_NAME,
        checks,
        pattern=pattern,
        generated_source=generated_source
    )
    print("Integration preview updated successfully:")
    print(f"  Preview: {workspace['name']}")
    print(f"  Workflow copy: {workflow_path}")
    print(f"  Diff: {diff_path}")
    print(f"  Metadata: {metadata_path}")
    print("  Original workflow: unchanged")


def _load_generated_workflow(code_path):
    project_path = Path.cwd().resolve()
    generated_path = Path(code_path).resolve()
    try:
        display_path = generated_path.relative_to(project_path)
    except ValueError as error:
        raise IntegrationError(
            f"Generated code must be inside the project: {generated_path}"
        ) from error
    if not generated_path.is_file() or generated_path.suffix.lower() != ".py":
        raise IntegrationError(f"Generated Python file not found: {generated_path}")
    metadata_path = generated_path.parent / "metadata" / f"{generated_path.stem}.json"
    if not metadata_path.is_file():
        raise IntegrationError(
            "Metadata file is required to revalidate saved code: "
            f"{metadata_path}"
        )
    generated_code = generated_path.read_text(encoding="utf-8")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise IntegrationError(f"Invalid generated workflow metadata: {error}") from error
    steps = metadata.get("steps", [])
    if not steps:
        raise IntegrationError("Saved workflow metadata does not contain scientific steps.")
    validated_plan = {
        "goal": metadata.get("goal"),
        "workflow_name": metadata.get("workflow_name", generated_path.stem),
        "steps": steps,
        "status": "new_supported_workflow",
        "execution_status": "not_implemented",
        "implementation_notes": ["Loaded from a previously generated workflow."]
    }
    return display_path, generated_code, validated_plan


def integrate_saved_workflow(code_path, workspace):
    generated_path, generated_code, validated_plan = _load_generated_workflow(code_path)
    pattern = resolve_implementation_pattern(validated_plan["steps"])
    all_passed, checks, error_message = validate_generated_code(
        generated_code,
        validated_plan["steps"],
        pattern
    )
    _print_validation_checks(checks)
    if not all_passed:
        print(
            "[ERROR] Saved code validation failed. Reason: "
            f"{error_message if error_message else 'Semantic checks failed.'}"
        )
        return
    _create_preview(
        generated_code,
        validated_plan,
        checks,
        workspace,
        pattern=pattern,
        generated_source=generated_path
    )


def _open_preview_workspace(preview_name, new_preview_name, preview_source):
    try:
        if new_preview_name:
            workspace = create_preview_workspace(
                new_preview_name,
                preview_source or WORKFLOW_SOURCE_PATH
            )
            print(f"[PREVIEW] Created new preview: {workspace['name']}")
        elif preview_name:
            workspace = load_preview_workspace(preview_name)
            print(f"[PREVIEW] Using existing preview: {workspace['name']}")
        else:
            return None
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as error:
        raise IntegrationError(str(error)) from error
    print(f"          Workflow: {workspace['workflow']}\n")
    return workspace


def main(
    print_code=False,
    preview_name=None,
    new_preview_name=None,
    preview_source=None,
    integrate_generated=None
):
    print("======================================================================")
    print("                PHYLO_AGENT 2 - HP2NET ORCHESTRATOR                   ")
    print("======================================================================\n")
    workspace = _open_preview_workspace(
        preview_name,
        new_preview_name,
        preview_source
    )
    if integrate_generated:
        integrate_saved_workflow(integrate_generated, workspace)
        return

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

    discovery_source = workspace["workflow"] if workspace else WORKFLOW_SOURCE_PATH
    discovered_workflow = discover_implemented_workflow(
        proposed_steps,
        target_knowledge,
        workflow_file=discovery_source
    )
    if discovered_workflow:
        print(
            "[2.5/3] Existing implementation discovered in "
            f"{discovered_workflow['source']}: {discovered_workflow['function_name']}"
        )
        print(f"      -> Scientific steps: {discovered_workflow['steps']}")
        print(f"      -> apps.* calls: {discovered_workflow['apps']}\n")
        print("[3/3] Agent 2 skipped.")
        if workspace:
            print("      -> The requested workflow already exists in the selected preview.")
        else:
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
    _print_validation_checks(checks)
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

    if workspace:
        _create_preview(
            generated_code,
            validated_plan,
            checks,
            workspace,
            pattern=pattern
        )
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
        description="Plan, validate, and generate HP2NET Parsl workflows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""artifact commands:
  artifacts list [all|preview|generated]
      List active artifacts, optionally filtered by category.
  artifacts rename {preview|generated} OLD_NAME NEW_NAME
      Rename a preview or generated workflow.
  artifacts sync preview NAME
      Validate and accept manual changes made to a preview.
  artifacts remove {preview|generated} NAME [--yes]
      Move an artifact to recoverable trash.
  artifacts restore {preview|generated} NAME
      Restore the most recently removed artifact with this name.
"""
    )
    parser.add_argument(
        "--print-code",
        action="store_true",
        help="Print validated code without saving files."
    )
    preview_group = parser.add_mutually_exclusive_group()
    preview_group.add_argument(
        "--preview",
        metavar="PREVIEW_NAME",
        help="Reuse an existing named preview and update its workflow copy."
    )
    preview_group.add_argument(
        "--new-preview",
        metavar="PREVIEW_NAME",
        help="Create a new named preview from the original workflow."
    )
    parser.add_argument(
        "--preview-source",
        metavar="WORKFLOW_FILE",
        help=f"Source for --new-preview. Defaults to {WORKFLOW_SOURCE_PATH}."
    )
    parser.add_argument(
        "--integrate-generated",
        metavar="GENERATED_FILE",
        help="Revalidate and integrate an existing generated workflow into a preview."
    )
    subparsers = parser.add_subparsers(dest="command")
    artifacts_parser = subparsers.add_parser(
        "artifacts",
        help="List, rename, sync, remove, or restore workflow artifacts.",
        description=(
            "Manage previews and generated workflows without calling the agents "
            "or the LLM. Removed artifacts are moved to recoverable trash."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    artifact_actions = artifacts_parser.add_subparsers(
        dest="artifact_action",
        required=True
    )

    list_parser = artifact_actions.add_parser("list", help="List active artifacts.")
    list_parser.add_argument(
        "artifact_type",
        nargs="?",
        choices=("all", "preview", "generated"),
        default="all",
        help="Artifact category to list. Defaults to all."
    )

    rename_parser = artifact_actions.add_parser("rename", help="Rename an artifact.")
    rename_parser.add_argument(
        "artifact_type",
        choices=("preview", "generated"),
        help="Artifact category."
    )
    rename_parser.add_argument("old_name", help="Current artifact name.")
    rename_parser.add_argument("new_name", help="New artifact name.")

    sync_parser = artifact_actions.add_parser(
        "sync",
        help="Validate and accept manual changes made to a preview."
    )
    sync_parser.add_argument(
        "artifact_type",
        choices=("preview",),
        help="Artifact category. Currently only preview is supported."
    )
    sync_parser.add_argument("name", help="Preview name to synchronize.")

    remove_parser = artifact_actions.add_parser(
        "remove",
        help="Move an artifact to the recoverable trash."
    )
    remove_parser.add_argument(
        "artifact_type",
        choices=("preview", "generated"),
        help="Artifact category."
    )
    remove_parser.add_argument("name", help="Artifact name to move to trash.")
    remove_parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation."
    )

    restore_parser = artifact_actions.add_parser(
        "restore",
        help="Restore the most recently removed artifact with this name."
    )
    restore_parser.add_argument(
        "artifact_type",
        choices=("preview", "generated"),
        help="Artifact category."
    )
    restore_parser.add_argument("name", help="Artifact name to restore from trash.")

    arguments = parser.parse_args()
    if arguments.command == "artifacts":
        if any((
            arguments.print_code,
            arguments.preview,
            arguments.new_preview,
            arguments.preview_source,
            arguments.integrate_generated
        )):
            parser.error("Artifact commands cannot be combined with workflow options.")
        return arguments
    selected_preview = arguments.preview or arguments.new_preview
    if arguments.print_code and selected_preview:
        parser.error("--print-code cannot be combined with preview options.")
    if arguments.integrate_generated and not selected_preview:
        parser.error("--integrate-generated requires --preview or --new-preview.")
    if arguments.print_code and arguments.integrate_generated:
        parser.error("--print-code cannot be combined with --integrate-generated.")
    if arguments.preview_source and not arguments.new_preview:
        parser.error("--preview-source can only be used with --new-preview.")
    return arguments


if __name__ == "__main__":
    try:
        arguments = parse_arguments()
        if arguments.command == "artifacts":
            run_artifact_command(arguments)
        else:
            main(
                print_code=arguments.print_code,
                preview_name=arguments.preview,
                new_preview_name=arguments.new_preview,
                preview_source=arguments.preview_source,
                integrate_generated=arguments.integrate_generated
            )
    except ArtifactError as error:
        print(f"[ARTIFACT ERROR] {error}")
    except IntegrationError as error:
        print(f"[INTEGRATION ERROR] {error}")
    except GenerationCancelled:
        print("Execução encerrada sem gerar ou alterar arquivos.")
    except KeyboardInterrupt:
        print("\nSolicitação cancelada pelo usuário.")
