import ast
from contextlib import redirect_stderr, redirect_stdout
import inspect
import io
import json
import math
import os
import logging
import sys
from types import SimpleNamespace

FAKE_FILES = ["/mock/file_a", "/mock/file_b"]
LOOP_COUNT = len(FAKE_FILES)
MOCK_CONFIG_VALUES = {
    "workflow_core": 4,
    "workflow_node": 1,
    "workload": "local",
    "tree_method": "raxml",
    "network_method": "MP",
    "plot_networks": False,
    "raxml_dir": "raxml",
    "iqtree_dir": "iqtree",
    "mrbayes_dir": "mrbayes",
    "mbsum_dir": "mbsum",
    "bucky_dir": "bucky",
    "astral_dir": "astral",
    "quartet_maxcut_dir": "quartet_maxcut",
    "snaq_dir": "snaq",
    "phylonet_dir": "phylonet",
    "raxml_threads": 1,
    "iqtree_threads": 1,
    "snaq_threads": 1,
    "phylonet_threads": 1,
    "snaq_hmax": ["0", "1"],
    "phylonet_hmax": ["0", "1"],
    "phylonet_input": "phylonet.nex",
}

class MockConfig:
    def __init__(self):
        for name, value in MOCK_CONFIG_VALUES.items():
            setattr(self, name, value)

class CircularList:
    def __init__(self, slots):
        if not slots:
            raise ValueError
        self.list = [None for _ in range(slots)]
        self.index = 0
        self.max_index = len(self.list) - 1
    def next(self):
        if self.index == self.max_index:
            self.index = 0
        else:
            self.index += 1
        return self.list[self.index]
    def current(self, value):
        self.list[self.index] = value
        return

class StubApps:
    def __init__(self):
        self.trace = []
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        def call(*args, **kwargs):
            placeholder = f"<{name}#{len(self.trace)}>"
            self.trace.append(
                {"app": name, "args": args, "kwargs": kwargs, "returns": placeholder}
            )
            return placeholder
        return call

class StubParsl:
    AUTO_LOGNAME = "AUTO_LOGNAME"
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None

def _flatten(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    else:
        yield value

def _load_workflow_function(code_str):
    tree = ast.parse(code_str)
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    function_nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not function_nodes:
        return None, "No workflow function found."
    function_node = function_nodes[0]
    if function_node.decorator_list:
        decorators = ", ".join("@" + ast.unparse(d) for d in function_node.decorator_list)
        return None, (
            "The workflow function must be a plain Python function without decorators. "
            f"Remove: {decorators}."
        )
    stub_apps = StubApps()
    namespace = {
        "apps": stub_apps,
        "glob": SimpleNamespace(glob=lambda *a, **k: list(FAKE_FILES)),
        "os": os,
        "math": math,
        "floor": math.floor,
        "logging": logging,
        "logger": logging.getLogger("dry_run"),
        "parsl": StubParsl(),
        "wait_for_all": lambda futures: futures,
        "BioConfig": MockConfig,
        "CircularList": CircularList,
    }
    exec(compile(tree, "<generated_workflow>", "exec"), namespace)
    return (namespace[function_node.name], stub_apps), None

def verify_trace(trace, pattern, knowledge):
    errors = []
    expected_apps = list(pattern.get("apps", []))
    specs = (knowledge or {}).get("apps", {})
    results_by_app = {}
    for entry in trace:
        results_by_app.setdefault(entry["app"], []).append(entry["returns"])
    for app in expected_apps:
        count = len(results_by_app.get(app, []))
        cardinality = specs.get(app, {}).get("cardinality", "once")
        expected = LOOP_COUNT if cardinality.startswith("per_") else 1
        if count != expected:
            errors.append(f"apps.{app} ran {count} time(s) in the dry run, expected {expected}.")
    for index, app in enumerate(expected_apps):
        if index == 0:
            wanted = {"init_placeholder"}
        else:
            wanted = set(results_by_app.get(expected_apps[index - 1], []))
        for entry in trace:
            if entry["app"] != app:
                continue
            received = set(_flatten(entry["kwargs"].get("inputs", [])))
            if not received & wanted:
                previous = "prepare_to_run" if index == 0 else f"apps.{expected_apps[index - 1]}"
                errors.append(f"apps.{app} does not receive the result of {previous} in 'inputs'.")
                break
    return errors

def run_dry_run(code_str, pattern=None, knowledge=None):
    try:
        loaded, error = _load_workflow_function(code_str)
    except Exception as exc:
        return False, f"Workflow code could not be loaded: {exc!r}"
    if error:
        return False, error
    function, stub_apps = loaded
    context = {
        "basedir": {"dir": "/mock/run", "tree_method": "raxml"},
        "config": MockConfig(),
        "bio_config": MockConfig(),
        "prepare_to_run": "init_placeholder",
    }
    try:
        parameters = inspect.signature(function).parameters
        arguments = {name: context[name] for name in parameters if name in context}
        function(**arguments)
    except Exception as exc:
        return False, f"Dry run raised {type(exc).__name__}: {exc}"
    if pattern:
        trace_errors = verify_trace(stub_apps.trace, pattern, knowledge)
        if trace_errors:
            return False, " ".join(trace_errors)
    return True, stub_apps.trace


def _trace_summary(trace):
    return [
        {"app": entry["app"], "returns": entry["returns"]}
        for entry in trace
    ]


def main():
    try:
        payload = json.load(sys.stdin)
        captured_output = io.StringIO()
        with redirect_stdout(captured_output), redirect_stderr(captured_output):
            ok, result = run_dry_run(
                payload.get("code", ""),
                payload.get("pattern"),
                payload.get("knowledge"),
            )
        response = {
            "ok": ok,
            "result": _trace_summary(result) if ok else str(result),
        }
    except BaseException as error:
        response = {
            "ok": False,
            "result": f"Isolated dry run failed: {type(error).__name__}: {error}",
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
