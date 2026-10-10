"""
phylo_agent2.py
===========================
Parsl Python code generator agent, backed by local Ollama LLMs, coupled with deterministic AST-based syntax and semantic validation.
"""

import ast
import json
import os
import re
import warnings
from collections import deque

from llm_client import GenerationCancelled, generate_stream

warnings.filterwarnings("ignore", category=SyntaxWarning)

OLLAMA_API_ENDPOINT = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "llama3.1:8b"

ORIGINAL_KB_PATH = "knowledge/hp2net_knowledge.json"
TEST_KB_PATH = "knowledge/code_discovery_knowledge.json"
WORKFLOW_SOURCE_PATH = "parsl_workflow.py"

IMPLEMENTATION_SPEC = {
    "framework": "Parsl",
    "workflow_file": "parsl_workflow.py",
    "output_format": "one Python function starting with def",
    "rules": [
        "Use only imports, decorators, applications and utility functions present in the implementation context.",
        "Preserve existing HP2Net resource handling and parameter passing conventions.",
        "Do not invent external APIs.",
        "Do not invent HP2Net APIs.",
        "Do not invent modules, classes, decorators or functions.",
        "Implement strictly the validated tool sequence and its required HP2Net implementation pattern.",
        "Do not add tools before, after, or between the validated steps unless required by the implementation pattern.",
        "Ensure function calls respect the exact signature contracts (required arguments and input types) defined in apps.py.",
        "Do not invent configuration attributes or file paths. Use only configuration fields present in bioconfig.py and filesystem paths established by the implementation pattern.",
        "Do not emit optional arguments merely because they exist in the app signature. Emit an argument only when the implementation pattern requires it or when the original HP2Net workflow uses it."
    ]
}

IMPLEMENTATION_PATTERNS = {
    ("BUCKy", "Quartet MaxCut"): {
        "apps": [
            "setup_bucky_data",
            "bucky",
            "setup_bucky_output",
            "setup_qmc_data",
            "quartet_maxcut",
            "setup_qmc_output"
        ],
        "multiplicity": {
            "setup_bucky_data": "once",
            "bucky": "once_per_prune_file",
            "setup_bucky_output": "once_after_bucky",
            "setup_qmc_data": "once_after_bucky_output",
            "quartet_maxcut": "once",
            "setup_qmc_output": "once_after_quartet_maxcut"
        },
        "description": (
            "1. Execute apps.setup_bucky_data(basedir, config, inputs=prepare_to_run)\n"
            "2. Wait for setup_bucky_data using wait_for_all([ret_pre_bucky])\n"
            "3. Discover prune trees using glob.glob(os.path.join(basedir['dir'], 'bucky', '*.txt'))\n"
            "4. Execute apps.bucky once per prune file in a loop passing prune_file=prune_tree and inputs=[ret_pre_bucky]\n"
            "5. Execute apps.setup_bucky_output(basedir, config, inputs=ret_bucky)\n"
            "6. Execute apps.setup_qmc_data(basedir, config, inputs=[ret_post_bucky])\n"
            "7. Execute apps.quartet_maxcut(basedir, config, inputs=[ret_pre_qmc])\n"
            "8. Execute apps.setup_qmc_output(basedir, config, inputs=[ret_qmc]) to obtain ret_post_qmc\n"
            "9. Iterate over config.snaq_hmax\n"
            "10. For each h value, execute apps.snaq(basedir=basedir, config=config, hmax=h, inputs=[ret_post_qmc])\n"
            "11. Return the list of SNaQ results"
        )
    },
    ("BUCKy", "Quartet MaxCut", "SNaQ"): {
        "apps": [
            "setup_bucky_data",
            "bucky",
            "setup_bucky_output",
            "setup_qmc_data",
            "quartet_maxcut",
            "setup_qmc_output",
            "snaq"
        ],
        "multiplicity": {
            "setup_bucky_data": "once",
            "bucky": "once_per_prune_file",
            "setup_bucky_output": "once_after_bucky",
            "setup_qmc_data": "once_after_bucky_output",
            "quartet_maxcut": "once",
            "setup_qmc_output": "once_after_quartet_maxcut",
            "snaq": "loop_hmax"
        },
        "description": (
            "1. Execute apps.setup_bucky_data(basedir, config, inputs=prepare_to_run)\n"
            "2. Wait for setup_bucky_data using wait_for_all([ret_pre_bucky])\n"
            "3. Discover prune trees using glob.glob(os.path.join(basedir['dir'], 'bucky', '*.txt'))\n"
            "4. Execute apps.bucky once per prune file in a loop passing prune_file=prune_tree and inputs=[ret_pre_bucky]\n"
            "5. Execute apps.setup_bucky_output(basedir, config, inputs=ret_bucky)\n"
            "6. Execute apps.setup_qmc_data(basedir, config, inputs=[ret_post_bucky])\n"
            "7. Execute apps.quartet_maxcut(basedir, config, inputs=[ret_pre_qmc])\n"
            "8. Execute apps.setup_qmc_output(basedir, config, inputs=[ret_qmc]) to obtain ret_post_qmc\n"
            "9. Iterate over config.snaq_hmax\n"
            "10. For each h value, execute apps.snaq(basedir=basedir, config=config, hmax=h, inputs=[ret_post_qmc])\n"
            "11. Return the list of SNaQ results"
        )
    },
    ("RAxML", "ASTRAL"): {
        "apps": [
            "raxml",
            "setup_tree_output",
            "astral"
        ],
        "multiplicity": {
            "raxml": "once_per_gene",
            "setup_tree_output": "once_after_raxml",
            "astral": "once"
        },
        "description": (
            "1. Discover gene alignments using datalist = glob.glob(os.path.join(basedir['dir'], 'input', 'phylip', '*.phy'))\n"
            "2. Initialize ret_tree = []\n"
            "3. For each input_file in datalist, execute apps.raxml(basedir=basedir, config=config, inputs=prepare_to_run, input_file=input_file) and append the result to ret_tree\n"
            "4. Execute apps.setup_tree_output(basedir=basedir, config=config, inputs=ret_tree) to obtain ret_sad\n"
            "5. Execute apps.astral(basedir=basedir, config=config, inputs=[ret_sad])\n"
            "6. Return the result of apps.astral"
        )
    },
    ("MrBayes", "MBSUM"): {
        "apps": [
            "mrbayes",
            "mbsum"
        ],
        "multiplicity": {
            "mrbayes": "once_per_gene",
            "mbsum": "once_per_gene"
        },
        "description": (
            "1. Discover input nexus alignments using datalist = glob.glob(os.path.join(basedir['dir'], 'input', 'nexus', '*.nex'))\n"
            "2. Initialize ret_mbsum = []\n"
            "3. For each input_file in datalist, execute apps.mrbayes(basedir=basedir, config=config, input_file=input_file, inputs=prepare_to_run)\n"
            "4. For the same input_file, execute apps.mbsum(basedir=basedir, config=config, input_file=input_file, inputs=[ret_mb])\n"
            "5. Append each MBSUM result to ret_mbsum\n"
            "6. Return ret_mbsum"
        )
    },
    ("RAxML", "root_tree", "PhyloNet"): {
        "apps": [
            "raxml",
            "setup_tree_output",
            "root_tree",
            "setup_phylonet_data",
            "phylonet"
        ],
        "multiplicity": {
            "raxml": "once_per_gene",
            "setup_tree_output": "once_after_raxml",
            "root_tree": "once",
            "setup_phylonet_data": "once_per_hmax",
            "phylonet": "once_per_hmax"
        },
        "description": (
            "1. Discover gene alignments using datalist = glob.glob(os.path.join(basedir['dir'], 'input', 'phylip', '*.phy'))\n"
            "2. Initialize ret_tree = []\n"
            "3. For each input_file in datalist, execute apps.raxml(basedir=basedir, config=config, inputs=prepare_to_run, input_file=input_file) and append the result to ret_tree\n"
            "4. Execute apps.setup_tree_output(basedir=basedir, config=config, inputs=ret_tree) to obtain ret_sad\n"
            "5. Execute apps.root_tree(basedir=basedir, config=config, inputs=[ret_sad]) to obtain ret_rooted\n"
            "6. Compute out_dir = os.path.join(basedir['dir'], config.phylonet_dir)\n"
            "7. For each h in config.phylonet_hmax, execute apps.setup_phylonet_data(basedir=basedir, config=config, hmax=h, inputs=[ret_rooted]) and store the result as ret_spd\n"
            "8. For the same h, construct filename = os.path.join(out_dir, basedir['tree_method'] + '_' + h + '_' + config.phylonet_input)\n"
            "9. For the same h, execute apps.phylonet(basedir=basedir, config=config, input_file=filename, inputs=[ret_spd])\n"
            "10. Append each PhyloNet result to result\n"
            "11. Return result"
        )
    }
}

def read_json_file(file_path):
    """Load and parse a JSON knowledge base.
    Input:
        file_path (str): Path to the JSON file.
    Output:
        dict: Parsed JSON content.
    Raises:
        FileNotFoundError: If the specified file does not exist.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as file_stream:
        return json.load(file_stream)

def extract_available_apps(file_path="apps.py"):
    """Extract function names defined in the Parsl apps module.
    Input:
        file_path (str): Path to the Python file containing Parsl app definitions.
    Output:
        set[str]: Set of function names defined in the file.
    """
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
    """Extract function signatures, decorators, and docstrings from Parsl apps.
    Input:
        file_path (str): Path to the Python file containing Parsl app definitions.
    Output:
        str: Formatted application contracts for inclusion in the LLM prompt.
    """
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
    """Load Parsl app contracts for inclusion in the Agent 2 prompt.
    Input:
        None.
    Output:
        str: Formatted function signatures, decorators, and docstrings extracted from apps.py.
    """
    return extract_app_signatures("apps.py")

def normalize_text(raw_text):
    """Normalize tool names and related terminology for comparison.
    Input:
        raw_text (str): Tool name or text to normalize.
    Output:
        str: Lowercase normalized text with known naming variants replaced by canonical forms.: str: Normalized text string.
    """
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
    """Map proposed tool-name variants to canonical HP2Net tool names.
    Input:
        proposed_steps (list[str]): Tool sequence proposed by the LLM.
        knowledge_base (dict): HP2Net knowledge base containing canonical tool definitions.
    Output:
        list[str]: Tool sequence using canonical HP2Net names where available.
    """
    canonical_tools = knowledge_base.get("tools", {})
    canonical_by_normalized_name = {
        normalize_text(tool_name): tool_name
        for tool_name in canonical_tools
    }
    canonical_steps = []
    for proposed_tool in proposed_steps:
        normalized_tool = normalize_text(proposed_tool)
        if normalized_tool in canonical_by_normalized_name:
            canonical_steps.append(canonical_by_normalized_name[normalized_tool])
        else:
            canonical_steps.append(proposed_tool)
    return canonical_steps

def extract_requested_tools(user_prompt, knowledge):
    """Extract explicitly mentioned HP2Net tools from a user request.
    Input:
        user_prompt (str): Natural language workflow request.
        knowledge (dict): Knowledge base containing known HP2Net tools.
    Output:
        list[str]: Canonical tool names in their order of appearance.
    """
    known_tools = list(knowledge.get("tools", {}).keys())
    found_tools = []
    normalized_prompt = normalize_text(user_prompt)
    for tool in known_tools:
        normalized_tool = normalize_text(tool)
        if normalized_tool in normalized_prompt:
            position = normalized_prompt.find(normalized_tool)
            found_tools.append((position, tool))
    found_tools.sort(key=lambda x: x[0])
    dedup_tools = []
    for _, tool in found_tools:
        if not dedup_tools or dedup_tools[-1] != tool:
            dedup_tools.append(tool)
    return dedup_tools

def build_interface_graph(knowledge):
    graph = {}
    tools = knowledge.get("tools", {})
    for tool in tools:
        graph[tool] = []
    interfaces = knowledge.get("interfaces", [])
    for iface in interfaces:
        if iface.get("supported", False):
            producer = iface.get("producer")
            consumer = iface.get("consumer")
            if producer in graph:
                graph[producer].append(consumer)
            else:
                graph[producer] = [consumer]
    return graph

def find_path(graph, start, end):
    if start not in graph or end not in graph:
        return None
    if start == end:
        return [start]
    queue = deque([[start]])
    visited = {start}
    while queue:
        path = queue.popleft()
        node = path[-1]
        for neighbor in graph.get(node, []):
            if neighbor == end:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])
    return None

def resolve_workflow_path(requested_tools, knowledge):
    if not requested_tools:
        return []
    if len(requested_tools) == 1:
        return requested_tools
    graph = build_interface_graph(knowledge)
    resolved_path = [requested_tools[0]]
    for i in range(len(requested_tools) - 1):
        start_tool = requested_tools[i]
        end_tool = requested_tools[i + 1]
        sub_path = find_path(graph, start_tool, end_tool)
        if not sub_path:
            return None
        resolved_path.extend(sub_path[1:])
    return resolved_path

def resolve_implementation_pattern(steps):
    tuple_steps = tuple(steps)
    return IMPLEMENTATION_PATTERNS.get(tuple_steps)

def tool_to_app_name(tool_name):
    """Convert a canonical knowledge-base tool name to its apps.py symbol."""
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
        scientific_steps = [
            app_to_tool[app_name]
            for app_name in app_sequence
            if app_name in app_to_tool
        ]
        if scientific_steps == requested_steps:
            return {
                "function_name": function.name,
                "steps": scientific_steps,
                "apps": app_sequence,
                "source": workflow_file
            }
    return None

def validate_endpoints(steps, requested_tools):
    """Validate preservation of the requested workflow endpoints (starting and ending tools).
    Input:
        steps (list[str]): Tool sequence proposed by Agent 1.
        requested_tools (list[str]): Tools explicitly requested by the user.
    Output:
        tuple[bool, str | None]: Validation status and an error message if the first or last requested tool is not preserved.
    """
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
    """Validate compatibility between consecutive workflow steps.
    Input:
        steps (list[str]): Proposed ordered tool sequence.
        knowledge (dict): Knowledge base containing interface definitions.
    Output:
        tuple[bool, str | None]: Validation status and an error message if a required interface is missing or unsupported.
    """
    interfaces = knowledge.get("interfaces", [])
    for i in range(len(steps) - 1):
        producer = steps[i]
        consumer = steps[i + 1]
        producer_norm = normalize_text(producer)
        consumer_norm = normalize_text(consumer)
        match = next(
            (
                iface for iface in interfaces 
                if normalize_text(iface.get("producer", "")) == producer_norm 
                and normalize_text(iface.get("consumer", "")) == consumer_norm
            ), 
            None
        )
        if not match:
            return False, f"No interface declared between {producer} and {consumer}."
        if not match.get("supported", False):
            return False, f"Interface between {producer} and {consumer} declared but not supported."
    return True, None

def validate_composition(steps, knowledge):
    requested_steps = list(steps)
    known_tools = set(knowledge.get("tools", {}).keys())
    unknown_tools = [tool for tool in requested_steps if tool not in known_tools]
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
    workflows = knowledge.get("workflows", {})
    if isinstance(workflows, dict):
        for workflow_name, workflow in workflows.items():
            if isinstance(workflow, dict):
                workflow_steps = workflow.get("steps", [])
                wf_status = workflow.get("status", workflow.get("execution_status", "implemented"))
                is_implemented = (wf_status == "implemented") or workflow.get("implemented", False)
            elif isinstance(workflow, list):
                workflow_steps = workflow
                is_implemented = True
            else:
                continue
            if requested_steps == workflow_steps:
                if is_implemented:
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

ALLOWED_WORKFLOW_GLOBALS = {
    "apps",
    "glob",
    "os",
    "wait_for_all",
    "BioConfig",
    "dict",
    "list",
    "str",
    "int",
    "float",
    "bool",
    "set",
    "tuple",
    "parsl",
    "print",
    "len"
}

def validate_workflow_names(tree):
    function_nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
    ]
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
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        allowed_names.add(elt.id)
    undefined_names = []
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in allowed_names:
                undefined_names.append(node.id)
    undefined_names = sorted(set(undefined_names))
    if undefined_names:
        return False, "Undefined workflow name(s): " + ", ".join(undefined_names)
    return True, None

def validate_generated_code(code_str, expected_steps, pattern=None, apps_file="apps.py"):
    checks = {
        "AST": "FAILED",
        "Required apps": "FAILED",
        "No extra apps": "FAILED",
        "App symbols": "FAILED",
        "Signature arguments": "FAILED",
        "Implementation pattern": "FAILED",
        "Workflow names": "FAILED"
    }
    try:
        tree = ast.parse(code_str)
        checks["AST"] = "PASSED"
    except SyntaxError as e:
        return False, checks, f"SyntaxError: {e}"
    names_ok, names_reason = validate_workflow_names(tree)
    if names_ok:
        checks["Workflow names"] = "PASSED"
    if pattern:
        expected_app_calls = [app.lower() for app in pattern["apps"]]
    else:
        expected_app_calls = [step.lower().replace(" ", "_").replace("-", "_") for step in expected_steps]
    called_apps = set()
    app_calls_ast = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "apps":
                    called_apps.add(node.func.attr)
                    app_calls_ast.append(node)
    missing_tools = [tool for tool in expected_app_calls if tool not in called_apps]
    if not missing_tools:
        checks["Required apps"] = "PASSED"
    extra_tools = [tool for tool in called_apps if tool not in expected_app_calls]
    if not extra_tools:
        checks["No extra apps"] = "PASSED"
    existing_apps = extract_available_apps(apps_file)
    non_existent = [tool for tool in called_apps if tool not in existing_apps]
    if not non_existent:
        checks["App symbols"] = "PASSED"
    signature_errors = []
    if os.path.exists(apps_file):
        try:
            with open(apps_file, "r", encoding="utf-8") as f:
                apps_tree = ast.parse(f.read(), filename=apps_file)
            app_func_nodes = {
                node.name: node for node in ast.walk(apps_tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for call_node in app_calls_ast:
                func_name = call_node.func.attr
                if func_name in app_func_nodes:
                    target_def = app_func_nodes[func_name]
                    positional_args = target_def.args.args
                    defaults_count = len(target_def.args.defaults)
                    num_required = len(positional_args) - defaults_count
                    required_arg_names = [arg.arg for arg in positional_args[:num_required]]
                    passed_keywords = {kw.arg for kw in call_node.keywords if kw.arg is not None}
                    num_positional_passed = len(call_node.args)
                    for idx, req_arg in enumerate(required_arg_names):
                        if idx >= num_positional_passed and req_arg not in passed_keywords:
                            signature_errors.append(f"Call to 'apps.{func_name}' is missing required argument '{req_arg}'.")
        except Exception as e:
            signature_errors.append(f"Failed to inspect app signatures: {e}")
    if not signature_errors:
        checks["Signature arguments"] = "PASSED"
    else:
        err_details = "; ".join(signature_errors)
    pattern_errors = []
    if pattern:
        for app in pattern["apps"]:
            if app not in called_apps:
                pattern_errors.append(f"Missing required pattern app 'apps.{app}'.")
        multiplicity = pattern.get("multiplicity", {})
        for app_name, mult in multiplicity.items():
            if mult in ["once_per_gene", "once_per_prune_file"]:
                app_in_loop = False
                for node in ast.walk(tree):
                    if isinstance(node, ast.For):
                        for inner_node in ast.walk(node):
                            if isinstance(inner_node, ast.Call) and isinstance(inner_node.func, ast.Attribute):
                                if inner_node.func.attr == app_name:
                                    app_in_loop = True
                                    break
                if not app_in_loop:
                    pattern_errors.append(f"App 'apps.{app_name}' must be executed inside a for-loop iterating over input files.")
        for app_name, mult in multiplicity.items():
            if mult in ["once_after_raxml", "once_after_bucky", "once_after_mrbayes"]:
                setup_call = next((c for c in app_calls_ast if c.func.attr == app_name), None)
                if setup_call:
                    inputs_kw = next((kw for kw in setup_call.keywords if kw.arg == "inputs"), None)
                    if inputs_kw and isinstance(inputs_kw.value, ast.Name):
                        pass
                    else:
                        pattern_errors.append(f"App 'apps.{app_name}' inputs parameter must receive the list accumulator of prior calls.")
    if not pattern_errors:
        checks["Implementation pattern"] = "PASSED"
    else:
        pattern_details = "; ".join(pattern_errors)
    all_passed = all(status == "PASSED" for status in checks.values())
    error_messages = []
    if not names_ok:
        error_messages.append(names_reason)
    if signature_errors:
        error_messages.append(err_details)
    if pattern_errors:
        error_messages.append(pattern_details)
    err_msg = "; ".join(error_messages) if error_messages else ""
    return all_passed, checks, err_msg

def extract_python_code(content: str) -> str:
    if "```python" in content:
        content = content.split("```python")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    lines = content.splitlines()
    code_lines = []
    found_def_or_import = False
    for line in lines:
        if line.startswith("def ") or line.startswith("import ") or line.startswith("from ") or line.startswith("@"):
            found_def_or_import = True
        if found_def_or_import:
            code_lines.append(line)
    return "\n".join(code_lines).strip()

def run_agent1_planner(user_prompt, original_knowledge, requested_tools):
    system_prompt = (
        "You are an expert computational biology workflow planner for HP2Net.\n"
        "Translate the user's request into an ordered sequence of HP2Net tools.\n\n"
        f"EXPLICITLY REQUESTED TOOLS: {json.dumps(requested_tools)}\n\n"
        "HARD CONSTRAINTS:\n"
        "1. Preserve the first and last tools explicitly requested by the user.\n"
        "2. If the requested endpoints are not directly connected, find a valid path between them using the original HP2Net knowledge base.\n"
        "3. Insert ONLY intermediate tools that are required by a valid interface in the original KB graph.\n"
        "4. Do NOT replace either requested endpoint.\n"
        "5. Do NOT add tools before the first requested tool or after the last requested tool.\n"
        "6. If EXPLICITLY REQUESTED TOOLS has only 1 item, output EXACTLY that 1 item.\n"
        "7. If no valid path connects the endpoints in the original KB, output only the requested tools as steps.\n\n"
        "EXAMPLE:\n"
        "If the user requests BUCKy and SNaQ (endpoints: ['BUCKy', 'SNaQ']) and the original KB defines\n"
        "the chain BUCKy -> Quartet MaxCut -> SNaQ, output:\n"
        "[\"BUCKy\", \"Quartet MaxCut\", \"SNaQ\"]\n\n"
        f"Available original HP2Net knowledge:\n{json.dumps(original_knowledge, indent=2)}\n\n"
        "Output JSON with keys 'goal' (string) and 'steps' (list of strings)."
    )
    payload = {
        "model": MODEL_NAME,
        "prompt": f"System: {system_prompt}\nUser request: {user_prompt}",
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
            "num_predict": 512
        }
    }
    try:
        raw_text = generate_stream(
            OLLAMA_API_ENDPOINT,
            payload,
            label="Agent 1"
        ).strip()
        return json.loads(raw_text)
    except GenerationCancelled:
        raise
    except Exception as e:
        print(f"[AGENT 1 ERROR] Communication or parsing failure: {e}")
        return {"goal": "", "steps": []}

def run_agent2_test_generator(validated_plan, pattern, spec, implementation_context):
    steps = validated_plan.get("steps", [])
    steps_str = " -> ".join(steps)
    workflow_name = validated_plan.get("workflow_name", "custom_workflow").lower().replace("-", "_")
    pattern_instruction = ""
    if pattern:
        pattern_instruction = (
            f"REQUIRED HP2Net IMPLEMENTATION PATTERN:\n{pattern.get('description', '')}\n\n"
            f"LIST OF HP2Net APPS TO CALL IN ORDER:\n{json.dumps(pattern.get('apps', []), indent=2)}\n\n"
            "Do not substitute or bypass this pattern with direct calls between scientific tools."
        )
    few_shot_example = '''
FEW-SHOT EXAMPLE OF EXPECTED OUTPUT:

def raxml_astral(basedir, config, prepare_to_run):
    datalist = glob.glob(
        os.path.join(basedir["dir"], "input", "phylip", "*.phy")
    )

    ret_tree = []

    for input_file in datalist:
        ret_tree.append(
            apps.raxml(
                basedir=basedir,
                config=config,
                inputs=prepare_to_run,
                input_file=input_file
            )
        )

    ret_sad = apps.setup_tree_output(
        basedir=basedir,
        config=config,
        inputs=ret_tree
    )

    ret_ast = apps.astral(
        basedir=basedir,
        config=config,
        inputs=[ret_sad]
    )

    return ret_ast
'''
    system_prompt = (
        "You are Agent 2: Test & Code Implementation Agent for HP2Net.\n"
        "Your task is ONLY to generate the single Python function for the requested steps and required implementation pattern.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. DO NOT output existing functions like raxml_snaq or iqtree_snaq.\n"
        "2. Implement ONLY the target steps chain and its associated HP2Net implementation pattern.\n"
        "3. Output ONLY valid executable Python code without markdown blocks or text.\n"
        "4. Pay strict attention to required positional and keyword parameters in AVAILABLE APPS CONTRACTS.\n"
        "5. Do not invent configuration attributes or file paths. Use only configuration fields present in bioconfig.py and filesystem paths established by the implementation pattern.\n"
        "6. Do not emit optional arguments merely because they exist in the app signature. Emit an argument only when the implementation pattern requires it or when the original HP2Net workflow uses it.\n\n"
        f"{few_shot_example}"
    )
    user_prompt = (
        f"SCIENTIFIC WORKFLOW STEPS: {steps_str}\n"
        f"FUNCTION NAME: {workflow_name}\n\n"
        f"{pattern_instruction}\n\n"
        f"SPECIFICATION RULES:\n{json.dumps(spec.get('rules', []), indent=2)}\n\n"
        f"AVAILABLE APPS CONTRACTS:\n{implementation_context}\n\n"
        f"Generate Python function {workflow_name}:"
    )
    payload = {
        "model": MODEL_NAME,
        "prompt": f"System: {system_prompt}\nUser: {user_prompt}",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 8192,
            "num_predict": 1024
        }
    }
    try:
        raw_content = generate_stream(
            OLLAMA_API_ENDPOINT,
            payload,
            label="Agent 2"
        ).strip()
        return extract_python_code(raw_content)
    except GenerationCancelled:
        raise
    except Exception as e:
        return f"# AGENT 2 ERROR: Failed to generate Python code: {e}"

def main():
    print("======================================================================")
    print("                PHYLO_AGENT 2 - HP2NET ORCHESTRATOR                   ")
    print("======================================================================\n")
    try:
        original_knowledge = read_json_file(ORIGINAL_KB_PATH)
        test_knowledge = read_json_file(TEST_KB_PATH)
    except Exception as e:
        print(f"[ERROR] Failed to load knowledge base files: {e}")
        return
    user_request = input("Enter workflow request: ").strip()
    if not user_request:
        user_request = "Use BUCKy to perform concordance analysis and then use SNaQ to infer a phylogenetic network."
        print(f"[INFO] Using default request: '{user_request}'\n")
    requested_tools = extract_requested_tools(user_request, original_knowledge)
    print(f"[0/3] Extracted requested tools: {requested_tools}")
    print("[1/3] Running Agent 1 (Planner over original HP2Net KB)...")
    agent1_output = run_agent1_planner(user_request, original_knowledge, requested_tools)
    proposed_steps = agent1_output.get("steps", [])
    proposed_steps = canonicalize_tool_names(proposed_steps, original_knowledge)
    goal = agent1_output.get("goal", user_request)
    print(f"      -> Goal: {goal}")
    print(f"      -> Proposed steps: {proposed_steps}\n")
    if not proposed_steps:
        print("[ERROR] Agent 1 did not generate a valid step sequence.")
        return

    known_tools = set(original_knowledge.get("tools", {})) | set(test_knowledge.get("tools", {}))
    unknown_proposed_tools = [
        tool for tool in proposed_steps
        if tool not in known_tools
    ]
    if unknown_proposed_tools:
        print(
            "[REJECTED BY PYTHON VALIDATOR] Unknown tool(s) proposed: "
            + ", ".join(unknown_proposed_tools)
        )
        print("[3/3] Agent 2 skipped.")
        return

    graph_resolved_steps = resolve_workflow_path(
        requested_tools,
        test_knowledge
    )

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
    validation = validate_composition(proposed_steps, test_knowledge)
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

    discovered_workflow = discover_implemented_workflow(
        proposed_steps,
        test_knowledge
    )
    if discovered_workflow:
        print(
            "[2.5/3] Existing implementation discovered in "
            f"{discovered_workflow['source']}: {discovered_workflow['function_name']}"
        )
        print(f"      -> Scientific steps: {discovered_workflow['steps']}")
        print(f"      -> apps.* calls: {discovered_workflow['apps']}\n")
        print("[3/3] Agent 2 skipped.")
        print("      -> The requested workflow already exists in the source code.")
        return

    pattern = resolve_implementation_pattern(proposed_steps)
    if pattern:
        print(f"[2.5/3] Resolved HP2Net implementation pattern: {pattern['apps']}\n")
    print("[3/3] Running Agent 2 (Parsl Code Generator)...")
    implementation_context = load_apps_context()
    generated_code = run_agent2_test_generator(
        validated_plan,
        pattern,
        IMPLEMENTATION_SPEC,
        implementation_context
    )
    all_passed, checks, err_msg = validate_generated_code(generated_code, proposed_steps, pattern)
    print("Code Validation:")
    for check_name, check_status in checks.items():
        print(f"  {check_name}: {check_status}")
    print()
    if not all_passed:
        print(f"[ERROR] Code validation failed. Reason: {err_msg if err_msg else 'Semantic checks failed.'}")
        return
    print("======================================================================")
    print("CODE GENERATED BY AGENT 2:")
    print("======================================================================")
    print(generated_code)
    print("======================================================================")

if __name__ == "__main__":
    try:
        main()
    except GenerationCancelled:
        print("Execução encerrada sem gerar ou alterar arquivos.")
    except KeyboardInterrupt:
        print("\nSolicitação cancelada pelo usuário.")
