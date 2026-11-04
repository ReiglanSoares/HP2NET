import ast
import os
from .dry_run import run_dry_run
from .knowledge import extract_available_apps

ALLOWED_WORKFLOW_GLOBALS = {
    "apps", "glob", "os", "wait_for_all", "BioConfig", "dict", "list",
    "str", "int", "float", "bool", "set", "tuple", "parsl", "print", "len"
}
LOOP_NODES = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.GeneratorExp)

def validate_workflow_names(tree):
    function_nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if not function_nodes:
        return False, "No workflow function found."
    function = function_nodes[0]
    allowed_names = set(ALLOWED_WORKFLOW_GLOBALS)
    for arg in function.args.args:
        allowed_names.add(arg.arg)
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            allowed_names.add(node.id)
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                allowed_names.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for element in node.target.elts:
                    if isinstance(element, ast.Name):
                        allowed_names.add(element.id)
    undefined_names = sorted({
        node.id for node in ast.walk(function)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in allowed_names
    })
    if undefined_names:
        return False, "Undefined workflow name(s): " + ", ".join(undefined_names)
    return True, None

def collect_app_calls(tree):
    calls = []
    def visit(node, depth):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "apps"
        ):
            calls.append((node.lineno, node.col_offset, node.func.attr, depth))
        next_depth = depth + 1 if isinstance(node, LOOP_NODES) else depth
        for child in ast.iter_child_nodes(node):
            visit(child, next_depth)
    visit(tree, 0)
    return sorted(calls)

def validate_call_structure(tree, pattern, knowledge):
    errors = []
    calls = collect_app_calls(tree)
    expected = list(pattern.get("apps", [])) if pattern else []
    if not expected:
        return errors
    first_seen = []
    for _, _, name, _ in calls:
        if name not in first_seen:
            first_seen.append(name)
    extra = [name for name in first_seen if name not in expected]
    if extra:
        errors.append("Unexpected apps.* calls: " + ", ".join(extra) + ".")
    observed = [name for name in first_seen if name in expected]
    if observed != [name for name in expected if name in observed]:
        errors.append(
            "Wrong call order. Expected " + " -> ".join(expected)
            + " but found " + " -> ".join(observed) + "."
        )
    app_specs = (knowledge or {}).get("apps", {})
    for _, _, name, depth in calls:
        cardinality = app_specs.get(name, {}).get("cardinality")
        if cardinality is None:
            continue
        if cardinality.startswith("per_") and depth == 0:
            errors.append(f"apps.{name} must be called inside a loop ({cardinality}).")
        elif cardinality == "once" and depth > 0:
            errors.append(f"apps.{name} must be called once, outside any loop.")
    return sorted(set(errors))

def validate_generated_code(code_str, expected_steps, pattern=None, apps_file="apps.py", knowledge=None):
    checks = {
        "AST": "FAILED", "Required apps": "FAILED", "No extra apps": "FAILED",
        "App symbols": "FAILED", "Signature arguments": "FAILED",
        "Implementation pattern": "PASSED", "Workflow names": "FAILED",
        "Call structure": "FAILED"
    }
    try:
        tree = ast.parse(code_str)
        checks["AST"] = "PASSED"
    except SyntaxError as error:
        return False, checks, f"SyntaxError: {error}"
    names_ok, names_reason = validate_workflow_names(tree)
    if names_ok:
        checks["Workflow names"] = "PASSED"
    expected_app_calls = (
        [app.lower() for app in pattern["apps"]]
        if pattern and pattern.get("apps") else
        [step.lower().replace(" ", "_").replace("-", "_") for step in expected_steps]
    )
    called_apps = set()
    app_calls_ast = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "apps"
        ):
            called_apps.add(node.func.attr)
            app_calls_ast.append(node)
    missing_apps = [app for app in expected_app_calls if app not in called_apps]
    if pattern and pattern.get("apps"):
        if not missing_apps:
            checks["Required apps"] = "PASSED"
    elif called_apps:
        checks["Required apps"] = "PASSED"
    extra_apps = []
    if pattern and pattern.get("apps"):
        extra_apps = sorted(called_apps - set(pattern["apps"]))
    if not extra_apps:
        checks["No extra apps"] = "PASSED"
    available_apps = extract_available_apps(apps_file)
    unknown_apps = [app for app in called_apps if app not in available_apps]
    if not unknown_apps:
        checks["App symbols"] = "PASSED"
    signature_errors = []
    if os.path.exists(apps_file):
        try:
            with open(apps_file, "r", encoding="utf-8") as file_stream:
                apps_tree = ast.parse(file_stream.read(), filename=apps_file)
            app_functions = {
                node.name: node for node in ast.walk(apps_tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for call_node in app_calls_ast:
                function_name = call_node.func.attr
                if function_name not in app_functions:
                    continue
                target = app_functions[function_name]
                required_count = len(target.args.args) - len(target.args.defaults)
                required_names = [arg.arg for arg in target.args.args[:required_count]]
                keywords = {k.arg for k in call_node.keywords if k.arg is not None}
                positional_count = len(call_node.args)
                for index, required_name in enumerate(required_names):
                    if index >= positional_count and required_name not in keywords:
                        signature_errors.append(
                            f"Call to 'apps.{function_name}' is missing required argument '{required_name}'."
                        )
        except Exception as error:
            signature_errors.append(f"Failed to inspect app signatures: {error}")
    if not signature_errors:
        checks["Signature arguments"] = "PASSED"
    structure_errors = validate_call_structure(tree, pattern, knowledge)
    dry_ok, dry_result = run_dry_run(code_str, pattern, knowledge)
    dry_error = None if dry_ok else dry_result
    if not structure_errors and dry_ok:
        checks["Call structure"] = "PASSED"
    errors = []
    if not names_ok:
        errors.append(names_reason)
    if checks["Required apps"] != "PASSED":
        if pattern and pattern.get("apps"):
            errors.append("Missing required apps.* calls: " + ", ".join(missing_apps) + ".")
        else:
            errors.append("No apps.* calls were found in the generated function.")
    if extra_apps:
        errors.append("Apps outside the pattern: " + ", ".join(extra_apps) + ".")
    if unknown_apps:
        errors.append("Unknown apps.* calls: " + ", ".join(sorted(unknown_apps)) + ".")
    if signature_errors:
        errors.append("; ".join(signature_errors))
    errors.extend(structure_errors)
    if dry_error:
        errors.append(dry_error)
    return all(status == "PASSED" for status in checks.values()), checks, " ".join(errors)

def extract_python_code(content):
    if "```python" in content:
        content = content.split("```python")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    lines = content.splitlines()
    code_lines = []
    found_code = False
    for line in lines:
        if line.startswith(("def ", "import ", "from ", "@")):
            found_code = True
        if found_code:
            code_lines.append(line)
    return "\n".join(code_lines).strip()