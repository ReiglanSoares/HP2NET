"""Deterministic renderer: builds the workflow function from the knowledge base."""

import re


class RenderError(Exception):
    """Raised when a tool chain cannot be rendered without the LLM."""


SCOPES = {
    "once": "once",
    "per_gene": "gene",
    "per_prune_file": "prune",
    "per_hmax": "hmax",
}
HMAX_FIELDS = {
    "setup_phylonet_data": "phylonet_hmax",
    "phylonet": "phylonet_hmax",
    "snaq": "snaq_hmax",
}
WAIT_AFTER = {"setup_bucky_data"}
PRUNE_GLOB = ("bucky", "*.txt")


def _options(requirements):
    options = set()
    for requirement in requirements:
        if isinstance(requirement, list):
            options.update(requirement)
        else:
            options.add(requirement)
    return options


def _function_name(raw_name):
    name = re.sub(r"\W+", "_", raw_name).strip("_").lower()
    if not name or name[0].isdigit():
        name = f"workflow_{name}"
    return name


def _input_location(app, specs, artifacts, initial):
    for option in sorted(_options(specs[app].get("requires", []))):
        if option not in initial:
            continue
        artifact = artifacts.get(option, {})
        directory = artifact.get("directory", "").strip("/")
        pattern = artifact.get("file_pattern")
        if directory and pattern:
            segments = ", ".join(repr(part) for part in directory.split("/"))
            return segments, pattern
    raise RenderError(
        f"apps.{app} has no initial input artifact with a directory and a file pattern."
    )


def render_workflow(steps, knowledge, function_name):
    tools = knowledge.get("tools", {})
    specs = knowledge.get("apps", {})
    artifacts = knowledge.get("artifacts", {})
    initial = set(knowledge.get("initial_artifacts", []))

    apps = []
    for tool in steps:
        if tool not in tools:
            raise RenderError(f"Unknown tool: {tool}")
        for app in tools[tool].get("apps", []):
            if app not in apps:
                apps.append(app)
    if not apps:
        raise RenderError("No apps to render.")

    blocks = []
    previous = None
    for app in apps:
        spec = specs.get(app)
        if spec is None:
            raise RenderError(f"apps.{app} has no specification.")
        scope = SCOPES.get(spec.get("cardinality"))
        if scope is None:
            raise RenderError(f"apps.{app} has an unsupported cardinality.")
        available = initial if previous is None else set(specs[previous].get("produces", []))
        if not _options(spec.get("requires", [])) & available:
            source = previous or "the initial artifacts"
            raise RenderError(f"apps.{app} is not fed by {source}; the chain is not linear.")
        if blocks and scope in ("gene", "hmax") and blocks[-1]["scope"] == scope:
            blocks[-1]["apps"].append(app)
        else:
            blocks.append({"scope": scope, "apps": [app]})
        previous = app

    body = []
    incoming = "prepare_to_run"
    result = None
    result_is_list = False
    for block in blocks:
        names, scope = block["apps"], block["scope"]
        last = names[-1]
        result = f"ret_{last}"
        result_is_list = scope != "once"

        if scope == "once":
            body.append(f"{result} = apps.{last}(basedir=basedir, config=config, inputs={incoming})")
            if last in WAIT_AFTER:
                body.append(f"wait_for_all([{result}])")
            incoming = f"[{result}]"

        elif scope == "gene":
            segments, pattern = _input_location(names[0], specs, artifacts, initial)
            body.append(f"datalist = glob.glob(os.path.join(basedir['dir'], {segments}, {pattern!r}))")
            body.append(f"{result} = []")
            body.append("for input_file in datalist:")
            call_inputs = incoming
            for app in names[:-1]:
                body.append(
                    f"    ret_{app} = apps.{app}(basedir=basedir, config=config, "
                    f"input_file=input_file, inputs={call_inputs})"
                )
                call_inputs = f"[ret_{app}]"
            body.append(
                f"    {result}.append(apps.{last}(basedir=basedir, config=config, "
                f"input_file=input_file, inputs={call_inputs}))"
            )
            incoming = result

        elif scope == "prune":
            if len(names) != 1:
                raise RenderError("Prune-file loops with several apps are not supported.")
            folder, pattern = PRUNE_GLOB
            body.append(f"prune_files = glob.glob(os.path.join(basedir['dir'], {folder!r}, {pattern!r}))")
            body.append(f"{result} = []")
            body.append("for prune_tree in prune_files:")
            body.append(
                f"    {result}.append(apps.{last}(basedir=basedir, config=config, "
                f"prune_file=prune_tree, inputs={incoming}))"
            )
            incoming = result

        else:
            fields = {HMAX_FIELDS.get(app) for app in names}
            if None in fields or len(fields) != 1:
                raise RenderError(f"Cannot determine the hmax field for: {', '.join(names)}.")
            field = fields.pop()
            if "phylonet" in names:
                body.append("out_dir = os.path.join(basedir['dir'], config.phylonet_dir)")
            body.append(f"{result} = []")
            body.append(f"for h in config.{field}:")
            call_inputs = incoming
            for app in names[:-1]:
                body.append(
                    f"    ret_{app} = apps.{app}(basedir=basedir, config=config, "
                    f"hmax=h, inputs={call_inputs})"
                )
                call_inputs = f"[ret_{app}]"
            if last == "phylonet":
                body.append(
                    "    filename = os.path.join(out_dir, basedir['tree_method'] + '_' + h + '_' "
                    "+ config.phylonet_input)"
                )
                body.append(
                    f"    {result}.append(apps.phylonet(basedir=basedir, config=config, "
                    f"input_file=filename, inputs={call_inputs}))"
                )
            else:
                body.append(
                    f"    {result}.append(apps.{last}(basedir=basedir, config=config, "
                    f"hmax=h, inputs={call_inputs}))"
                )
            incoming = result

    body.append(f"return {result}" if result_is_list else f"return [{result}]")
    header = f"def {_function_name(function_name)}(config, basedir, prepare_to_run):"
    return "\n".join([header] + ["    " + line for line in body]) + "\n"
