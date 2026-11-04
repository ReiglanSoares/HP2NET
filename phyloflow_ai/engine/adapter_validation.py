import ast
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from .graph import simulate_tool_chain


ALLOWED_CARDINALITIES = {"once", "per_gene", "per_prune_file", "per_hmax"}


def _result(section, name, status, message=""):
    return {
        "section": section,
        "name": name,
        "status": status,
        "message": message,
    }


def _parse_python(path, section, name, results):
    if not path.is_file():
        results.append(_result(section, name, "FAILED", f"File not found: {path}"))
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        results.append(_result(section, name, "FAILED", str(error)))
        return None
    results.append(_result(section, name, "PASSED", str(path)))
    return tree


def _read_json(path, section, name, results):
    if not path.is_file():
        results.append(_result(section, name, "FAILED", f"File not found: {path}"))
        return None
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        results.append(_result(section, name, "FAILED", str(error)))
        return None
    if not isinstance(content, dict):
        results.append(_result(section, name, "FAILED", "Top-level JSON must be an object."))
        return None
    results.append(_result(section, name, "PASSED", str(path)))
    return content


def _function_signatures(tree):
    signatures = {}
    if tree is None:
        return signatures
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            required = len(node.args.args) - len(node.args.defaults)
            signatures[node.name] = [argument.arg for argument in node.args.args[:required]]
    return signatures


def _config_names(tree):
    names = set()
    if tree is None:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    names.add(target.attr)
    return names


def _requirement_options(requirement):
    return requirement if isinstance(requirement, list) else [requirement]


def validate_adapter(adapter, patterns, project_root="."):
    project_path = Path(project_root).resolve()
    results = []
    workflow_path = project_path / adapter.workflow_source
    apps_path = project_path / adapter.apps_source
    config_path = project_path / getattr(adapter, "config_source", "bioconfig.py")

    if not project_path.is_dir():
        return [_result("Project", "Directory", "FAILED", f"Not found: {project_path}")]
    results.append(_result("Project", "Directory", "PASSED", str(project_path)))
    if os.access(project_path, os.W_OK):
        results.append(_result("Project", "Workspace access", "PASSED"))
    else:
        results.append(_result("Project", "Workspace access", "FAILED", "Project is not writable."))

    workflow_tree = _parse_python(workflow_path, "Project files", adapter.workflow_source, results)
    apps_tree = _parse_python(apps_path, "Project files", adapter.apps_source, results)
    config_tree = _parse_python(config_path, "Project files", config_path.name, results)
    app_signatures = _function_signatures(apps_tree)
    config_names = _config_names(config_tree)

    original = _read_json(
        Path(adapter.original_knowledge), "Knowledge", "Original knowledge JSON", results
    )
    knowledge = _read_json(
        Path(adapter.target_knowledge), "Knowledge", "Target knowledge JSON", results
    )
    if knowledge is None:
        return results

    artifacts = knowledge.get("artifacts", {})
    apps = knowledge.get("apps", {})
    tools = knowledge.get("tools", {})
    initial_artifacts = knowledge.get("initial_artifacts", [])
    for key, value in (("artifacts", artifacts), ("apps", apps), ("tools", tools)):
        status = "PASSED" if isinstance(value, dict) and value else "FAILED"
        message = "" if status == "PASSED" else f"'{key}' must be a non-empty object."
        results.append(_result("Knowledge", key, status, message))

    unknown_initial = sorted(set(initial_artifacts) - set(artifacts))
    results.append(_result(
        "Knowledge",
        "Initial artifacts",
        "FAILED" if unknown_initial else "PASSED",
        "Unknown: " + ", ".join(unknown_initial) if unknown_initial else "",
    ))

    for app_name, specification in apps.items():
        if app_name not in app_signatures:
            results.append(_result(
                "Apps", app_name, "FAILED", f"apps.{app_name} is declared but not defined in {apps_path.name}."
            ))
        requirements = specification.get("requires", [])
        produced = specification.get("produces", [])
        referenced = {
            option for requirement in requirements for option in _requirement_options(requirement)
        } | set(produced)
        unknown = sorted(referenced - set(artifacts))
        if unknown:
            results.append(_result("Artifacts", app_name, "FAILED", "Unknown: " + ", ".join(unknown)))
        cardinality = specification.get("cardinality")
        if cardinality not in ALLOWED_CARDINALITIES:
            results.append(_result(
                "Cardinality", app_name, "FAILED", f"Unsupported value: {cardinality!r}"
            ))
        call_hint = specification.get("call_hint", "")
        missing_placeholders = [token for token in ("{in}", "{out}") if token not in call_hint]
        if missing_placeholders:
            results.append(_result(
                "Call hints", app_name, "FAILED", "Missing " + ", ".join(missing_placeholders)
            ))

    if not any(result["section"] == "Apps" and result["status"] == "FAILED" for result in results):
        results.append(_result("Apps", "Declared app symbols", "PASSED", f"{len(apps)} checked"))
    if not any(result["section"] == "Artifacts" and result["status"] == "FAILED" for result in results):
        results.append(_result("Artifacts", "References", "PASSED"))
    if not any(result["section"] == "Cardinality" and result["status"] == "FAILED" for result in results):
        results.append(_result("Cardinality", "Values", "PASSED"))
    if not any(result["section"] == "Call hints" and result["status"] == "FAILED" for result in results):
        results.append(_result("Call hints", "Placeholders", "PASSED"))

    for tool_name, specification in tools.items():
        mapped_apps = specification.get("apps", [])
        unknown = [name for name in mapped_apps if name not in apps]
        if not mapped_apps or unknown:
            message = "No apps mapped." if not mapped_apps else "Unknown apps: " + ", ".join(unknown)
            results.append(_result("Tools", tool_name, "FAILED", message))
    if not any(result["section"] == "Tools" and result["status"] == "FAILED" for result in results):
        results.append(_result("Tools", "Mappings", "PASSED", f"{len(tools)} checked"))

    sequence_owners = defaultdict(list)
    config_references = set()
    for steps, pattern in patterns.items():
        label = " -> ".join(steps)
        pattern_apps = pattern.get("apps", [])
        sequence_owners[tuple(pattern_apps)].append(label)
        unknown_tools = [tool for tool in steps if tool not in tools]
        unknown_apps = [app for app in pattern_apps if app not in apps or app not in app_signatures]
        error, _ = simulate_tool_chain(list(steps), knowledge) if not unknown_tools else (None, None)
        messages = []
        if unknown_tools:
            messages.append("Unknown tools: " + ", ".join(unknown_tools))
        if unknown_apps:
            messages.append("Unknown apps: " + ", ".join(unknown_apps))
        if error:
            messages.append(error)
        status = "FAILED" if messages else "PASSED"
        results.append(_result("Patterns", label, status, " ".join(messages)))
        text = pattern.get("description", "")
        config_references.update(re.findall(r"\bconfig\.([A-Za-z_]\w*)", text))

    duplicates = [owners for owners in sequence_owners.values() if len(owners) > 1]
    results.append(_result(
        "Patterns",
        "Duplicate app sequences",
        "WARNING" if duplicates else "PASSED",
        "; ".join(" | ".join(owners) for owners in duplicates),
    ))

    for specification in apps.values():
        config_references.update(
            re.findall(r"\bconfig\.([A-Za-z_]\w*)", specification.get("call_hint", ""))
        )
    missing_config = sorted(config_references - config_names)
    results.append(_result(
        "Configuration",
        "Referenced fields",
        "FAILED" if missing_config else "PASSED",
        "Missing: " + ", ".join(missing_config) if missing_config else f"{len(config_references)} checked",
    ))
    return results


def print_adapter_report(adapter_name, results):
    print(f"PhyloFlowAI Adapter Validation: {adapter_name}")
    print()
    current_section = None
    for result in results:
        if result["section"] != current_section:
            current_section = result["section"]
            print(f"{current_section}:")
        suffix = f" - {result['message']}" if result["message"] else ""
        print(f"  {result['name']}: {result['status']}{suffix}")
    failed = sum(result["status"] == "FAILED" for result in results)
    warnings = sum(result["status"] == "WARNING" for result in results)
    print()
    print(f"Overall result: {'FAILED' if failed else 'PASSED'} ({failed} error(s), {warnings} warning(s))")
    return failed == 0
