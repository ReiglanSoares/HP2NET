import ast
import builtins
import difflib
import hashlib
from pathlib import Path


class IntegrationError(ValueError):
    pass


def _sha256(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _resolve_project_file(file_path, project_root="."):
    project_path = Path(project_root).resolve()
    resolved_path = Path(file_path).resolve()
    try:
        resolved_path.relative_to(project_path)
    except ValueError as error:
        raise IntegrationError(
            f"Integration target must be inside the project: {resolved_path}"
        ) from error
    if resolved_path.suffix.lower() != ".py":
        raise IntegrationError("Integration target must be a Python file.")
    if not resolved_path.is_file():
        raise IntegrationError(f"Integration target not found: {resolved_path}")
    return project_path, resolved_path


def _parse_source(source_code, filename, label):
    try:
        return ast.parse(source_code, filename=str(filename))
    except SyntaxError as error:
        raise IntegrationError(f"{label} contains invalid Python syntax: {error}") from error


def _extract_single_function(generated_tree):
    function_nodes = [
        node for node in generated_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    other_statements = [
        node for node in generated_tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    if len(function_nodes) != 1 or other_statements:
        raise IntegrationError(
            "Generated code must contain exactly one top-level workflow function."
        )
    return function_nodes[0]


def _app_call_sequence(function_node):
    app_calls = []
    for node in ast.walk(function_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "apps"
        ):
            app_calls.append((
                getattr(node, "lineno", 0),
                getattr(node, "col_offset", 0),
                node.func.attr
            ))
    return [name for _, _, name in sorted(app_calls)]


def _function_body_signature(function_node):
    body = list(function_node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    body_module = ast.Module(body=body, type_ignores=[])
    return ast.dump(body_module, annotate_fields=True, include_attributes=False)


def _function_global_names(function_node):
    local_names = {argument.arg for argument in function_node.args.args}
    local_names.update(argument.arg for argument in function_node.args.kwonlyargs)
    if function_node.args.vararg:
        local_names.add(function_node.args.vararg.arg)
    if function_node.args.kwarg:
        local_names.add(function_node.args.kwarg.arg)
    local_names.update(
        node.id for node in ast.walk(function_node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    )
    loaded_names = {
        node.id for node in ast.walk(function_node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return loaded_names - local_names - set(dir(builtins))


def _target_global_names(target_tree):
    names = set()
    for node in target_tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _is_main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    comparison = node.test
    if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
        return False
    values = [comparison.left, *comparison.comparators]
    has_name = any(isinstance(value, ast.Name) and value.id == "__name__" for value in values)
    has_main = any(
        isinstance(value, ast.Constant) and value.value == "__main__"
        for value in values
    )
    return has_name and has_main


def _insert_before_main_guard(original_code, generated_code, target_tree):
    main_guard = next((node for node in target_tree.body if _is_main_guard(node)), None)
    if not main_guard:
        return original_code.rstrip() + "\n\n\n" + generated_code + "\n"

    original_lines = original_code.splitlines(keepends=True)
    insertion_index = main_guard.lineno - 1
    before_guard = "".join(original_lines[:insertion_index])
    guard_and_after = "".join(original_lines[insertion_index:])
    trailing_newlines = len(before_guard) - len(before_guard.rstrip("\r\n"))
    separator_before = "\n" * max(0, 3 - trailing_newlines)
    return before_guard + separator_before + generated_code + "\n\n\n" + guard_and_after


def build_integration_preview(
    generated_code,
    workflow_path,
    baseline_workflow_path=None,
    project_root="."
):
    project_path, target_path = _resolve_project_file(workflow_path, project_root)
    if baseline_workflow_path:
        _, source_path = _resolve_project_file(baseline_workflow_path, project_root)
    else:
        source_path = target_path

    previous_preview_code = target_path.read_text(encoding="utf-8")
    original_code = source_path.read_text(encoding="utf-8")
    generated_code = generated_code.strip()
    if not generated_code:
        raise IntegrationError("Generated workflow code is empty.")

    generated_tree = _parse_source(generated_code, "generated_workflow.py", "Generated code")
    function_node = _extract_single_function(generated_tree)
    function_name = function_node.name

    target_tree = _parse_source(previous_preview_code, target_path, "Integration target")
    existing_functions = [
        node for node in target_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    existing_names = {node.name for node in existing_functions}
    if function_name in existing_names:
        raise IntegrationError(
            f"Workflow function '{function_name}' already exists in {target_path.name}."
        )

    generated_app_sequence = _app_call_sequence(function_node)
    if generated_app_sequence:
        duplicate_apps_function = next((
            existing_function for existing_function in existing_functions
            if _app_call_sequence(existing_function) == generated_app_sequence
        ), None)
        if duplicate_apps_function:
            raise IntegrationError(
                "Equivalent workflow app sequence already exists in function "
                f"'{duplicate_apps_function.name}': "
                + " -> ".join(f"apps.{name}" for name in generated_app_sequence)
            )

    generated_body_signature = _function_body_signature(function_node)
    equivalent_body_function = next((
        existing_function for existing_function in existing_functions
        if _function_body_signature(existing_function) == generated_body_signature
    ), None)
    if equivalent_body_function:
        raise IntegrationError(
            "Equivalent workflow code already exists in function "
            f"'{equivalent_body_function.name}'."
        )

    required_globals = _function_global_names(function_node)
    available_globals = _target_global_names(target_tree)
    missing_globals = sorted(required_globals - available_globals)
    if missing_globals:
        raise IntegrationError(
            "Integration target does not provide required global name(s): "
            + ", ".join(missing_globals)
        )

    preview_code = _insert_before_main_guard(
        previous_preview_code,
        generated_code,
        target_tree
    )
    preview_tree = _parse_source(preview_code, target_path.name, "Integrated preview")
    preview_matches = [
        node for node in preview_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(preview_matches) != 1:
        raise IntegrationError(
            f"Integrated preview does not contain exactly one '{function_name}' function."
        )

    original_hash = _sha256(original_code)
    previous_preview_hash = _sha256(previous_preview_code)
    if _sha256(source_path.read_text(encoding="utf-8")) != original_hash:
        raise IntegrationError("The source workflow changed while the preview was being built.")
    if _sha256(target_path.read_text(encoding="utf-8")) != previous_preview_hash:
        raise IntegrationError("The selected preview changed while the integration was being built.")

    relative_source = source_path.relative_to(project_path)
    relative_target = target_path.relative_to(project_path)
    diff_text = "".join(difflib.unified_diff(
        original_code.splitlines(keepends=True),
        preview_code.splitlines(keepends=True),
        fromfile=str(relative_source),
        tofile=str(relative_target)
    ))

    return {
        "function_name": function_name,
        "source_path": str(relative_source),
        "target_path": str(relative_target),
        "source_filename": target_path.name,
        "preview_code": preview_code,
        "diff": diff_text,
        "hashes": {
            "original_sha256": original_hash,
            "previous_preview_sha256": previous_preview_hash,
            "generated_sha256": _sha256(generated_code + "\n"),
            "preview_sha256": _sha256(preview_code)
        },
        "validation_checks": {
            "Generated function AST": "PASSED",
            "Target workflow AST": "PASSED",
            "Duplicate function name": "PASSED",
            "Duplicate app sequence": "PASSED",
            "Equivalent function body": "PASSED",
            "Required target globals": "PASSED",
            "Integrated preview AST": "PASSED",
            "Source workflow unchanged": "PASSED",
            "Selected preview unchanged during build": "PASSED"
        }
    }
