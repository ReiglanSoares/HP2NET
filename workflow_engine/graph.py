from collections import deque

from .knowledge import normalize_text


def build_interface_graph(knowledge):
    graph = {tool: [] for tool in knowledge.get("tools", {})}
    for interface in knowledge.get("interfaces", []):
        if interface.get("supported", False):
            producer = interface.get("producer")
            graph.setdefault(producer, []).append(interface.get("consumer"))
    return graph


def find_path(graph, start, end):
    if start not in graph or end not in graph:
        return None
    if start == end:
        return [start]
    pending = deque([[start]])
    visited = {start}
    while pending:
        path = pending.popleft()
        for neighbor in graph.get(path[-1], []):
            if neighbor == end:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(path + [neighbor])
    return None


def resolve_workflow_path(requested_tools, knowledge):
    if not requested_tools:
        return []
    if len(requested_tools) == 1:
        return requested_tools
    graph = build_interface_graph(knowledge)
    resolved_path = [requested_tools[0]]
    for start_tool, end_tool in zip(requested_tools, requested_tools[1:]):
        sub_path = find_path(graph, start_tool, end_tool)
        if not sub_path:
            return None
        resolved_path.extend(sub_path[1:])
    return resolved_path


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


def validate_interfaces(steps, knowledge):
    interfaces = knowledge.get("interfaces", [])
    for producer, consumer in zip(steps, steps[1:]):
        match = next((
            interface for interface in interfaces
            if normalize_text(interface.get("producer", "")) == normalize_text(producer)
            and normalize_text(interface.get("consumer", "")) == normalize_text(consumer)
        ), None)
        if not match:
            return False, f"No interface declared between {producer} and {consumer}."
        if not match.get("supported", False):
            return False, f"Interface between {producer} and {consumer} declared but not supported."
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
    valid_interfaces, interface_reason = validate_interfaces(requested_steps, knowledge)
    if not valid_interfaces:
        return {
            "status": "unsupported_composition",
            "execution_status": "unsupported",
            "implementation_notes": [interface_reason] if interface_reason else []
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
        "implementation_notes": ["Valid and supported path in target KB graph."]
    }
