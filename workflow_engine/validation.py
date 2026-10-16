import ast
import os

from .knowledge import extract_available_apps


ALLOWED_WORKFLOW_GLOBALS = {
    "apps", "glob", "os", "wait_for_all", "BioConfig", "dict", "list",
    "str", "int", "float", "bool", "set", "tuple", "parsl", "print", "len"
}


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


def validate_generated_code(code_str, expected_steps, pattern=None, apps_file="apps.py"):
    checks = {
        "AST": "FAILED", "Required apps": "FAILED", "No extra apps": "FAILED",
        "App symbols": "FAILED", "Signature arguments": "FAILED",
        "Implementation pattern": "FAILED", "Workflow names": "FAILED"
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
        if pattern else
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
    if not [app for app in expected_app_calls if app not in called_apps]:
        checks["Required apps"] = "PASSED"
    if not [app for app in called_apps if app not in expected_app_calls]:
        checks["No extra apps"] = "PASSED"
    if not [app for app in called_apps if app not in extract_available_apps(apps_file)]:
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
                keywords = {keyword.arg for keyword in call_node.keywords if keyword.arg is not None}
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

    pattern_errors = []
    if pattern:
        for app_name in pattern["apps"]:
            if app_name not in called_apps:
                pattern_errors.append(f"Missing required pattern app 'apps.{app_name}'.")
        for app_name, multiplicity in pattern.get("multiplicity", {}).items():
            if multiplicity in ["once_per_gene", "once_per_prune_file"]:
                app_in_loop = any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == app_name
                    for loop in ast.walk(tree) if isinstance(loop, ast.For)
                    for inner in ast.walk(loop)
                )
                if not app_in_loop:
                    pattern_errors.append(
                        f"App 'apps.{app_name}' must be executed inside a for-loop iterating over input files."
                    )
        for app_name, multiplicity in pattern.get("multiplicity", {}).items():
            if multiplicity in ["once_after_raxml", "once_after_bucky", "once_after_mrbayes"]:
                setup_call = next((call for call in app_calls_ast if call.func.attr == app_name), None)
                if setup_call:
                    inputs = next((keyword for keyword in setup_call.keywords if keyword.arg == "inputs"), None)
                    if not inputs or not isinstance(inputs.value, ast.Name):
                        pattern_errors.append(
                            f"App 'apps.{app_name}' inputs parameter must receive the list accumulator of prior calls."
                        )
    if not pattern_errors:
        checks["Implementation pattern"] = "PASSED"

    errors = []
    if not names_ok:
        errors.append(names_reason)
    if signature_errors:
        errors.append("; ".join(signature_errors))
    if pattern_errors:
        errors.append("; ".join(pattern_errors))
    return all(status == "PASSED" for status in checks.values()), checks, "; ".join(errors)


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
