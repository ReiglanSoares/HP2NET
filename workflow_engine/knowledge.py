import ast
import json
import os


def read_json_file(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as file_stream:
        return json.load(file_stream)


def extract_available_apps(file_path="apps.py"):
    if not os.path.exists(file_path):
        return set()
    with open(file_path, "r", encoding="utf-8") as file_stream:
        source_code = file_stream.read()
    try:
        syntax_tree = ast.parse(source_code, filename=file_path)
    except SyntaxError:
        return set()
    return {
        node.name for node in ast.walk(syntax_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def extract_app_signatures(file_path="apps.py"):
    if not os.path.exists(file_path):
        return ""
    with open(file_path, "r", encoding="utf-8") as file_stream:
        source_code = file_stream.read()
    try:
        syntax_tree = ast.parse(source_code, filename=file_path)
    except SyntaxError:
        return ""
    extracted_signatures = []
    for node in ast.walk(syntax_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decorators = [f"@{ast.unparse(decorator)}" for decorator in node.decorator_list]
            decorator_prefix = "\n".join(decorators) + "\n" if decorators else ""
            argument_names = []
            for arg in node.args.args:
                arg_str = arg.arg
                if arg.annotation:
                    arg_str += f": {ast.unparse(arg.annotation)}"
                argument_names.append(arg_str)
            function_signature = f"{decorator_prefix}def {node.name}({', '.join(argument_names)}):"
            function_docstring = ast.get_docstring(node)
            if function_docstring:
                function_signature += f'\n    """{function_docstring.strip()}"""'
            function_signature += "\n    pass\n"
            extracted_signatures.append(function_signature)
    return f"===== AVAILABLE APPS CONTRACTS ({file_path}) =====\n" + "\n".join(extracted_signatures)


def load_apps_context():
    return extract_app_signatures("apps.py")


def normalize_text(raw_text):
    lowercased_text = raw_text.lower()
    replacement_mappings = {
        ("iq-tree", "iq tree", "iqtree2", "iq-tree2", "iq tree 2"): "iqtree",
        ("phylo-net", "phylo net"): "phylonet",
        ("mr bayes", "mr-bayes"): "mrbayes",
        ("maximum pseudo likelihood", "maximum pseudo-likelihood"): "mpl",
        ("maximum parsimony",): "mp"
    }
    for variant_tuple, canonical_form in replacement_mappings.items():
        for variant in variant_tuple:
            lowercased_text = lowercased_text.replace(variant, canonical_form)
    return lowercased_text


def canonicalize_tool_names(proposed_steps, knowledge_base):
    canonical_by_normalized_name = {
        normalize_text(tool_name): tool_name
        for tool_name in knowledge_base.get("tools", {})
    }
    canonical_steps = []
    for proposed_tool in proposed_steps:
        normalized_tool = normalize_text(proposed_tool)
        canonical_steps.append(canonical_by_normalized_name.get(normalized_tool, proposed_tool))
    return canonical_steps


def extract_requested_tools(user_prompt, knowledge):
    found_tools = []
    normalized_prompt = normalize_text(user_prompt)
    for tool in knowledge.get("tools", {}):
        normalized_tool = normalize_text(tool)
        if normalized_tool in normalized_prompt:
            found_tools.append((normalized_prompt.find(normalized_tool), tool))
    found_tools.sort(key=lambda item: item[0])
    dedup_tools = []
    for _, tool in found_tools:
        if not dedup_tools or dedup_tools[-1] != tool:
            dedup_tools.append(tool)
    return dedup_tools
