from collections import deque
from .knowledge import normalize_text

def _options(requirement):
    return requirement if isinstance(requirement, list) else [requirement]
def app_dataflow_error(app, available, knowledge):
    spec = knowledge.get("apps", {}).get(app)
    if spec is None:
        return f"App '{app}' is not described in the KB."
    missing = [
        " or ".join(_options(r)) for r in spec.get("requires", [])
        if not any(o in available for o in _options(r))
    ]
    if missing:
        return f"'{app}' requires {missing}, which no earlier step produces."
    return None

def tool_interface(tool, knowledge):
    consumed, produced = set(), set()
    tool_spec = knowledge.get("tools", {}).get(tool, {})
    for app in tool_spec.get("apps", []):
        spec = knowledge.get("apps", {}).get(app, {})
        for requirement in spec.get("requires", []):
            options = _options(requirement)
            if not any(o in produced for o in options):
                consumed.update(options)
        produced.update(spec.get("produces", []))
    return consumed, produced

def simulate_tool_chain(steps, knowledge, require_adjacent=False):
    tools = knowledge.get("tools", {})
    available = set(knowledge.get("initial_artifacts", []))
    previous_tool, previous_produced = None, None
    for tool in steps:
        if tool not in tools or not tools[tool].get("apps"):
            return f"Tool '{tool}' has no app mapping in the KB.", available
        consumed, produced = tool_interface(tool, knowledge)
        if require_adjacent and previous_produced is not None and not (consumed & previous_produced):
            return (
                f"Linear generation cannot connect '{previous_tool}' directly to '{tool}'. "
                "This composition requires an explicit branch or merge.",
                available,
            )
        for app in tools[tool]["apps"]:
            error = app_dataflow_error(app, available, knowledge)
            if error:
                return error, available
            available.update(knowledge["apps"][app].get("produces", []))
        previous_tool, previous_produced = tool, produced
    return None, available


def producer_dependency_paths(artifact, knowledge, available=(), max_depth=8):
    """Return deterministic producer chains and their unresolved prerequisites."""
    apps = knowledge.get("apps", {})
    initial = set(knowledge.get("initial_artifacts", [])) | set(available)

    def resolve(target, trail, depth):
        if target in initial:
            return [{"apps": [], "unresolved_artifacts": []}]
        if depth <= 0 or target in trail:
            return [{"apps": [], "unresolved_artifacts": [target]}]
        producers = [name for name, spec in apps.items() if target in spec.get("produces", [])]
        if not producers:
            return [{"apps": [], "unresolved_artifacts": [target]}]
        paths = []
        for app_name in producers:
            partial = [{"apps": [], "unresolved_artifacts": []}]
            for requirement in apps[app_name].get("requires", []):
                options = _options(requirement)
                option_paths = []
                for option in options:
                    option_paths.extend(resolve(option, trail | {target}, depth - 1))
                best = min(
                    option_paths,
                    key=lambda item: (len(item["unresolved_artifacts"]), len(item["apps"])),
                )
                for item in partial:
                    item["apps"].extend(best["apps"])
                    item["unresolved_artifacts"].extend(best["unresolved_artifacts"])
            for item in partial:
                item["apps"].append(app_name)
                item["apps"] = list(dict.fromkeys(item["apps"]))
                item["unresolved_artifacts"] = sorted(set(item["unresolved_artifacts"]))
                paths.append(item)
        return paths

    paths = resolve(artifact, set(), max_depth)
    return sorted(paths, key=lambda item: (len(item["unresolved_artifacts"]), len(item["apps"])))

def plan_tool_path(requested_tools, knowledge, max_extra=4):
    tools = knowledge.get("tools", {})
    if not requested_tools or any(t not in tools for t in requested_tools):
        return None
    path = [requested_tools[0]]
    if simulate_tool_chain(path, knowledge, require_adjacent=True)[0]:
        return None
    for target in requested_tools[1:]:
        queue, seen, found = deque([[]]), set(), None
        while queue and found is None:
            segment = queue.popleft()
            if not simulate_tool_chain(
                path + segment + [target], knowledge, require_adjacent=True
            )[0]:
                found = segment + [target]
                break
            if len(segment) >= max_extra:
                continue
            for tool in tools:
                candidate = segment + [tool]
                error, available = simulate_tool_chain(
                    path + candidate, knowledge, require_adjacent=True
                )
                key = (tool, frozenset(available))
                if error is None and key not in seen:
                    seen.add(key)
                    queue.append(candidate)
        if found is None:
            return None
        path.extend(found)
    return path

def resolve_workflow_path(requested_tools, knowledge):
    return plan_tool_path(requested_tools, knowledge)

def validate_endpoints(steps, requested_tools):
    if not requested_tools:
        return True, None
    if not steps:
        return False, "Plan is empty."
    if steps[0] != requested_tools[0]:
        return False, f"First endpoint violation: expected '{requested_tools[0]}', got '{steps[0]}'."
    if steps[-1] != requested_tools[-1]:
        return False, f"Last endpoint violation: expected '{requested_tools[-1]}', got '{steps[-1]}'."
    return True, None

def validate_composition(steps, knowledge):
    requested_steps = list(steps)
    unknown_tools = [tool for tool in requested_steps if tool not in knowledge.get("tools", {})]
    if unknown_tools:
        return {
            "status": "invalid_workflow",
            "execution_status": "unsupported",
            "implementation_notes": [f"Unknown tool(s): {', '.join(unknown_tools)}."]
        }

    error, _ = simulate_tool_chain(requested_steps, knowledge)
    if error:
        return {
            "status": "unsupported_composition",
            "execution_status": "unsupported",
            "implementation_notes": [error]
        }

    for workflow_name, workflow in knowledge.get("workflows", {}).items():
        if isinstance(workflow, dict):
            workflow_steps = workflow.get("steps", [])
            status = workflow.get("status", workflow.get("execution_status", "implemented"))
            implemented = status == "implemented" or workflow.get("implemented", False)
        elif isinstance(workflow, list):
            workflow_steps, implemented = workflow, True
        else:
            continue
        if requested_steps == workflow_steps and implemented:
            return {
                "status": "existing_workflow",
                "execution_status": "implemented",
                "implementation_notes": [f"Implemented as part of {workflow_name}."]
            }

    return {
        "status": "new_supported_workflow",
        "execution_status": "not_implemented",
        "implementation_notes": ["Valid and supported artifact dataflow path in KB."]
    }
