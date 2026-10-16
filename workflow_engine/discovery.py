import ast
import os

from .settings import WORKFLOW_SOURCE_PATH


def tool_to_app_name(tool_name):
    return tool_name.lower().replace(" ", "_").replace("-", "")


def discover_implemented_workflow(steps, knowledge, workflow_file=WORKFLOW_SOURCE_PATH):
    if not os.path.exists(workflow_file):
        return None
    try:
        with open(workflow_file, "r", encoding="utf-8") as file_stream:
            workflow_tree = ast.parse(file_stream.read(), filename=workflow_file)
    except (OSError, SyntaxError):
        return None
    app_to_tool = {
        tool_to_app_name(tool_name): tool_name
        for tool_name in knowledge.get("tools", {})
    }
    requested_steps = list(steps)
    for function in workflow_tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        app_calls = []
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "apps"
            ):
                app_calls.append((getattr(node, "lineno", 0), node.func.attr))
        app_sequence = [name for _, name in sorted(app_calls)]
        scientific_steps = [app_to_tool[name] for name in app_sequence if name in app_to_tool]
        if scientific_steps == requested_steps:
            return {
                "function_name": function.name,
                "steps": scientific_steps,
                "apps": app_sequence,
                "source": workflow_file
            }
    return None
