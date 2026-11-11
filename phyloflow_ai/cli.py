"""Command-line entry point for AI-assisted workflow generation."""

import argparse
import json
import os
from pathlib import Path
import warnings

from .agents import run_agent1_planner, run_agent2_test_generator
from .llm import GenerationCancelled
from .engine.generated_assets import ArtifactError, run_artifact_command
from .engine.adapter_validation import print_adapter_report, validate_adapter
from .engine.knowledge_suggestions import (
    build_candidate_knowledge_patch,
    generate_knowledge_suggestions,
    print_candidate_knowledge_patch,
    print_knowledge_suggestions,
    save_knowledge_suggestions,
)
from .engine.source_ingestion import run_source_command
from .engine.discovery import (
    discover_implemented_workflow,
    tool_to_app_name,
)
from .engine.graph import (
    #build_interface_graph,
    #find_path,
    resolve_workflow_path,
    validate_composition,
    validate_endpoints,
    simulate_tool_chain,
    #validate_interfaces,
)
from .engine.integration import IntegrationError, build_integration_preview
from .engine.knowledge import (
    canonicalize_tool_names,
    extract_app_signatures,
    extract_available_apps,
    extract_requested_tools,
    load_apps_context,
    normalize_text,
    read_json_file,
)
from .adapters.hp2net.patterns import (
    IMPLEMENTATION_PATTERNS,
    build_dynamic_pattern,
    resolve_implementation_pattern,
)
from .adapters.hp2net import ADAPTER
from .adapters.hp2net.renderer import RenderError, render_workflow
from .engine.output import (
    create_preview_workspace,
    load_preview_workspace,
    save_generated_workflow,
    update_integration_preview,
)
from .adapters.hp2net.settings import (
    IMPLEMENTATION_SPEC,
    MODEL_NAME,
    OLLAMA_API_ENDPOINT,
    ORIGINAL_KB_PATH,
    TARGET_KB_PATH,
    WORKFLOW_SOURCE_PATH,
)
from .engine.validation import (
    ALLOWED_WORKFLOW_GLOBALS,
    extract_python_code,
    validate_generated_code,
    validate_workflow_names,
    strip_decorators,
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
    generated_source=None,
):
    integration_result = build_integration_preview(
        generated_code,
        workspace["workflow"],
        baseline_workflow_path=workspace["source"],
    )
    workflow_path, diff_path, metadata_path = update_integration_preview(
        workspace,
        integration_result,
        validated_plan,
        MODEL_NAME,
        checks,
        pattern=pattern,
        generated_source=generated_source,
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
        "implementation_notes": ["Loaded from a previously generated workflow."],
    }
    return display_path, generated_code, validated_plan

def integrate_saved_workflow(code_path, workspace, target_knowledge):
    generated_path, generated_code, validated_plan = _load_generated_workflow(code_path)
    pattern = resolve_implementation_pattern(validated_plan["steps"])
    if pattern is None:
        pattern = build_dynamic_pattern(validated_plan["steps"], target_knowledge)
    all_passed, checks, error_message = validate_generated_code(
        generated_code,
        validated_plan["steps"],
        pattern,
        knowledge=target_knowledge,
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
        generated_source=generated_path,
    )

def _open_preview_workspace(preview_name, new_preview_name, preview_source):
    try:
        if new_preview_name:
            workspace = create_preview_workspace(
                new_preview_name,
                preview_source or WORKFLOW_SOURCE_PATH,
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
    integrate_generated=None,
):
    print("======================================================================")
    print("              PHYLOFLOWAI - WORKFLOW ORCHESTRATOR                    ")
    print("======================================================================\n")
    workspace = _open_preview_workspace(
        preview_name,
        new_preview_name,
        preview_source,
    )
    try:
        original_knowledge = read_json_file(ORIGINAL_KB_PATH)
        target_knowledge = read_json_file(TARGET_KB_PATH)
    except Exception as error:
        print(f"[ERROR] Failed to load knowledge base files: {error}")
        return
    if integrate_generated:
        integrate_saved_workflow(integrate_generated, workspace, target_knowledge)
        return
    user_request = input("Enter workflow request: ").strip()
    if not user_request:
        user_request = "Use BUCKy to perform concordance analysis and then use SNaQ to infer a phylogenetic network."
        print(f"[INFO] Using default request: '{user_request}'\n")
    requested_tools = extract_requested_tools(user_request, original_knowledge)
    print(f"[0/3] Extracted requested tools: {requested_tools}")
    graph_resolved_steps = resolve_workflow_path(requested_tools, target_knowledge)
    if graph_resolved_steps:
        proposed_steps = graph_resolved_steps
        goal = f"Build a workflow for {', '.join(requested_tools)}"
        print(f"[1/3] Using deterministic graph resolution directly: {proposed_steps}")
    else:
        error_msg, available_artifacts = simulate_tool_chain(requested_tools, target_knowledge)
        print(f"[1/3] Scientific dataflow path resolution failed.")
        print(f"      -> Reason: {error_msg}")
        print(f"      -> Available artifacts at failure point: {list(available_artifacts)}")
        print("[STOPPED] Workflow request is scientifically invalid or unsupported by current app contracts.")
        return
    if not proposed_steps:
        print("[ERROR] Step sequence is empty.")
        return
    known_tools = set(original_knowledge.get("tools", {})) | set(target_knowledge.get("tools", {}))
    unknown_tools = [tool for tool in proposed_steps if tool not in known_tools]
    if unknown_tools:
        print("[REJECTED BY PYTHON VALIDATOR] Unknown tool(s) proposed: " + ", ".join(unknown_tools))
        print("[3/3] Agent 2 skipped.")
        return
    endpoints_valid, endpoint_error = validate_endpoints(
        proposed_steps,
        requested_tools
    )
    if not endpoints_valid:
        print(f"[REJECTED BY PYTHON VALIDATOR] {endpoint_error}")
        print("[3/3] Agent 2 skipped.")
        return
    print("[2.5/3] Searching for an existing source implementation...")
    discovery_source = workspace["workflow"] if workspace else WORKFLOW_SOURCE_PATH
    discovered_workflow = discover_implemented_workflow(
        proposed_steps,
        target_knowledge,
        workflow_file=discovery_source,
    )
    if discovered_workflow:
        print(
            "[2.5/3] Existing implementation discovered in "
            f"{discovered_workflow['source']}: "
            f"{discovered_workflow['function_name']}"
        )
        print(f"      -> Scientific steps: {discovered_workflow['steps']}")
        print(f"      -> apps.* calls: {discovered_workflow['apps']}")
        print("[3/3] Agent 2 skipped.")
        if workspace:
            print("      -> The requested workflow already exists in the selected preview.")
        else:
            print("      -> Matching workflow found in the source code.")
        return
    print("[2/3] Validating plan against Target KB Graph...")
    validation = validate_composition(
        proposed_steps,
        target_knowledge
    )
    validated_plan = {
        "goal": goal,
        "workflow_name": "-".join(proposed_steps),
        "steps": proposed_steps,
        "status": validation.get("status"),
        "execution_status": validation.get("execution_status"),
        "implementation_notes": validation.get("implementation_notes")
    }
    print(
        f"      -> Validation status: "
        f"{validated_plan['status']} "
        f"({validated_plan['execution_status']})"
    )
    print(
        f"      -> Notes: "
        f"{validated_plan['implementation_notes']}\n"
    )
    status = validated_plan["status"]
    if status == "existing_workflow":
        print(
            "[WARNING] The target KB marks this workflow as implemented, "
            "but source discovery did not find an exact matching sequence."
        )
        print(
            "[3/3] Agent 2 skipped to avoid generating a possible duplicate. "
            "Review the KB and source implementation."
        )
        return
    if status != "new_supported_workflow":
        print("[3/3] Agent 2 skipped.")
        print(
            "      -> Composition is not supported by the target KB graph."
        )
        return
    pattern = resolve_implementation_pattern(proposed_steps)
    if pattern:
        print(f"[2.5/3] Resolved HP2Net implementation pattern: {pattern['apps']}\n")
    else:
        print("[2.5/3] No static pattern found; building dynamic execution specification.\n")
        pattern = build_dynamic_pattern(proposed_steps, target_knowledge)


    max_attempts = 3
    generated_code = None
    all_passed = False
    checks = {}
    error_message = ""

    try:
        generated_code = render_workflow(
            proposed_steps,
            target_knowledge,
            validated_plan["workflow_name"]
        )
    except RenderError as error:
        print(f"[3/3] Template rendering unavailable ({error}); falling back to the LLM.")

    if generated_code:
        all_passed, checks, error_message = validate_generated_code(
            generated_code,
            proposed_steps,
            pattern,
            knowledge=target_knowledge
        )

        if not all_passed:
            print("[ERROR] Deterministic rendering failed validation.")
            print(f"      -> {error_message}")
            print(generated_code)
            return
        print("[3/3] Code rendered deterministically from the knowledge base.")
    else:
        print("[3/3] Running Agent 2 (Parsl Code Generator with self-correction)...")

        for attempt in range(1, max_attempts + 1):
            print(f"      -> Attempt {attempt} of {max_attempts}...")
            spec = IMPLEMENTATION_SPEC

            if attempt > 1 and error_message:
                spec = dict(IMPLEMENTATION_SPEC)
                spec["rules"] = list(IMPLEMENTATION_SPEC["rules"]) + [
                    "PREVIOUS CODE FAILED VALIDATION: " + error_message
                    + "\nPREVIOUS CODE:\n" + generated_code
                    + "\nReturn the complete corrected function. Change only what is needed "
                    "to fix these problems and keep everything else identical."
                ]

            generated_code = run_agent2_test_generator(
                validated_plan,
                pattern,
                spec,
                load_apps_context()
            )

            generated_code = strip_decorators(generated_code)

            all_passed, checks, error_message = validate_generated_code(
                generated_code,
                proposed_steps,
                pattern,
                knowledge=target_knowledge
            )

            if all_passed:
                print("      -> Code validation passed successfully!")
                break

            print(f"      -> Validation failed on attempt {attempt}: {error_message}")
            print(generated_code)
    print("\nCode Validation Results:")
    for check_name, check_status in checks.items():
        print(f"  {check_name}: {check_status}")
    print()
    if not all_passed:
        print(f"[ERROR] Code validation failed after {max_attempts} attempts. Reason: {error_message if error_message else 'Semantic checks failed.'}")
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
            pattern=pattern,
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
        description="PhyloFlowAI: plan, validate, and generate workflows through a project adapter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""asset commands:
  assets list [all|preview|generated]
  assets rename {preview|generated} OLD_NAME NEW_NAME
  assets sync preview NAME
  assets remove {preview|generated} NAME [--yes]
  assets restore {preview|generated} NAME
adapter commands:
  adapter validate [hp2net] [--suggestions] [--source-profile PROFILE]
source commands:
  sources ingest [--profile PROFILE] [--tool TOOL] [--no-web] [--force]
  sources list [--profile PROFILE]
  sources validate [--profile PROFILE]
""",
    )
    parser.add_argument(
        "--print-code",
        action="store_true",
        help="Print validated code without saving files."
    )
    parser.add_argument(
        "--adapter",
        choices=("hp2net",),
        default="hp2net",
        help="Project adapter to use. Currently HP2NET is supported.",
    )
    parser.add_argument(
        "--project",
        default=".",
        metavar="DIRECTORY",
        help="Project directory to analyze. Defaults to the current directory.",
    )
    preview_group = parser.add_mutually_exclusive_group()
    preview_group.add_argument("--preview", metavar="PREVIEW_NAME", help="Reuse an existing named preview.")
    preview_group.add_argument("--new-preview", metavar="PREVIEW_NAME", help="Create a new named preview.")
    parser.add_argument(
        "--preview-source", metavar="WORKFLOW_FILE",
        help=f"Source for --new-preview. Defaults to {WORKFLOW_SOURCE_PATH}."
    )
    parser.add_argument(
        "--integrate-generated", metavar="GENERATED_FILE",
        help="Revalidate and integrate saved generated code into a preview."
    )
    subparsers = parser.add_subparsers(dest="command")
    assets_parser = subparsers.add_parser(
        "assets", aliases=["artifacts"],
        help="Manage previews and generated workflows without calling the LLM."
    )
    actions = assets_parser.add_subparsers(dest="artifact_action", required=True)
    list_parser = actions.add_parser("list", help="List active assets.")
    list_parser.add_argument(
        "artifact_type", nargs="?", choices=("all", "preview", "generated"),
        default="all"
    )
    rename_parser = actions.add_parser("rename", help="Rename an asset.")
    rename_parser.add_argument("artifact_type", choices=("preview", "generated"))
    rename_parser.add_argument("old_name")
    rename_parser.add_argument("new_name")
    sync_parser = actions.add_parser("sync", help="Accept validated manual preview changes.")
    sync_parser.add_argument("artifact_type", choices=("preview",))
    sync_parser.add_argument("name")
    remove_parser = actions.add_parser("remove", help="Move an asset to recoverable trash.")
    remove_parser.add_argument("artifact_type", choices=("preview", "generated"))
    remove_parser.add_argument("name")
    remove_parser.add_argument("--yes", action="store_true", help="Skip confirmation.")
    restore_parser = actions.add_parser("restore", help="Restore an asset from trash.")
    restore_parser.add_argument("artifact_type", choices=("preview", "generated"))
    restore_parser.add_argument("name")

    adapter_parser = subparsers.add_parser(
        "adapter",
        help="Validate a project adapter without calling the LLM.",
    )
    adapter_actions = adapter_parser.add_subparsers(dest="adapter_action", required=True)
    validate_parser = adapter_actions.add_parser(
        "validate",
        help="Validate project files, knowledge contracts, apps, and patterns.",
    )
    validate_parser.add_argument(
        "target_adapter", nargs="?", choices=("hp2net",), default="hp2net"
    )
    validate_parser.add_argument(
        "--suggestions",
        action="store_true",
        help="Analyze failed patterns using only relevant scientific sources and save candidate suggestions.",
    )
    validate_parser.add_argument(
        "--source-profile",
        choices=("official", "user_only", "combined"),
        default="combined",
        help="Scientific source index used by suggestions. Defaults to combined.",
    )

    sources_parser = subparsers.add_parser(
        "sources",
        help="Ingest, list, and validate scientific document sources.",
    )
    source_actions = sources_parser.add_subparsers(dest="source_action", required=True)
    ingest_parser = source_actions.add_parser(
        "ingest", help="Extract and index registered local or remote documents."
    )
    ingest_parser.add_argument(
        "--profile",
        choices=("official", "user_only", "combined"),
        default="combined",
        help="Source profile to build. Defaults to combined.",
    )
    ingest_parser.add_argument("--tool", help="Ingest only one registered tool.")
    ingest_parser.add_argument(
        "--no-web", action="store_true", help="Do not download remote-only sources."
    )
    ingest_parser.add_argument(
        "--force", action="store_true", help="Redownload and rebuild available sources."
    )
    ingest_parser.add_argument(
        "--user-only",
        action="store_true",
        help="Build the index only from sources registered by the user.",
    )
    list_sources_parser = source_actions.add_parser(
        "list", help="List source ingestion results for one profile."
    )
    list_sources_parser.add_argument(
        "--profile", choices=("official", "user_only", "combined"), default="combined"
    )
    validate_sources_parser = source_actions.add_parser(
        "validate", help="Validate one source profile and PDF support."
    )
    validate_sources_parser.add_argument(
        "--profile", choices=("official", "user_only", "combined"), default="combined"
    )

    arguments = parser.parse_args()
    if arguments.command in ("assets", "artifacts"):
        if any((arguments.print_code, arguments.preview, arguments.new_preview,
                arguments.preview_source, arguments.integrate_generated)):
            parser.error("Asset commands cannot be combined with workflow options.")
        return arguments
    if arguments.command in ("adapter", "sources"):
        if any((arguments.print_code, arguments.preview, arguments.new_preview,
                arguments.preview_source, arguments.integrate_generated)):
            parser.error("Adapter commands cannot be combined with workflow options.")
        if (
            arguments.command == "sources"
            and arguments.source_action == "ingest"
            and arguments.user_only
            and arguments.profile != "combined"
        ):
            parser.error("--user-only cannot be combined with --profile; use --profile user_only.")
        if (
            arguments.command == "adapter"
            and not arguments.suggestions
            and arguments.source_profile != "combined"
        ):
            parser.error("--source-profile is only used together with --suggestions.")
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

def run():
    try:
        arguments = parse_arguments()
        project_path = Path(arguments.project).resolve()
        if not project_path.is_dir():
            raise IntegrationError(f"Project directory not found: {project_path}")
        os.chdir(project_path)
        if arguments.command in ("assets", "artifacts"):
            run_artifact_command(arguments)
            return 0
        elif arguments.command == "sources":
            return 0 if run_source_command(arguments) else 1
        elif arguments.command == "adapter":
            validation_results = validate_adapter(
                ADAPTER, IMPLEMENTATION_PATTERNS, project_path
            )
            passed = print_adapter_report(
                ADAPTER.name,
                validation_results,
            )
            if arguments.suggestions:
                knowledge = read_json_file(ADAPTER.target_knowledge)
                report = generate_knowledge_suggestions(
                    ADAPTER.name,
                    validation_results,
                    knowledge,
                    source_profile=arguments.source_profile,
                )
                candidate_patch = build_candidate_knowledge_patch(
                    report,
                    knowledge,
                    ADAPTER.target_knowledge,
                    workflow_source=project_path / ADAPTER.workflow_source,
                )
                report["candidate_knowledge_patch"] = candidate_patch
                print_knowledge_suggestions(report)
                print_candidate_knowledge_patch(candidate_patch)
                suggestion_path = save_knowledge_suggestions(report)
                print(f"\nComplete suggestion report saved to: {suggestion_path}")
                print("Adapter knowledge: unchanged")
            return 0 if passed else 1
        else:
            main(
                print_code=arguments.print_code,
                preview_name=arguments.preview,
                new_preview_name=arguments.new_preview,
                preview_source=arguments.preview_source,
                integrate_generated=arguments.integrate_generated,
            )
            return 0
    except ArtifactError as error:
        print(f"[ASSET ERROR] {error}")
        return 1
    except IntegrationError as error:
        print(f"[INTEGRATION ERROR] {error}")
        return 1
    except GenerationCancelled:
        print("Execution completed without generating or modifying any files.")
        return 130
    except KeyboardInterrupt:
        print("\nExecution cancelled by the user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(run())
