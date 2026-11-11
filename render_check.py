import sys

from phyloflow_ai.adapters.hp2net.patterns import build_dynamic_pattern, resolve_implementation_pattern
from phyloflow_ai.adapters.hp2net.renderer import RenderError, render_workflow
from phyloflow_ai.adapters.hp2net.settings import TARGET_KB_PATH
from phyloflow_ai.engine.knowledge import read_json_file
from phyloflow_ai.engine.validation import validate_generated_code

CHAINS = [
    ["RAxML", "ASTRAL"],
    ["IQ-TREE", "ASTRAL"],
    ["RAxML", "ASTRAL", "SNaQ"],
    ["RAxML", "root_tree", "PhyloNet"],
    ["MrBayes", "MBSUM"],
    ["MrBayes", "MBSUM", "BUCKy", "Quartet MaxCut"],
    ["MrBayes", "MBSUM", "BUCKy", "Quartet MaxCut", "SNaQ"],
    ["RAxML", "IQ-TREE"],
]

knowledge = read_json_file(TARGET_KB_PATH)
for steps in CHAINS:
    label = " -> ".join(steps)
    try:
        code = render_workflow(steps, knowledge, "-".join(steps))
    except RenderError as error:
        print(f"{label}: NOT RENDERABLE - {error}")
        continue
    pattern = resolve_implementation_pattern(steps) or build_dynamic_pattern(steps, knowledge)
    ok, checks, message = validate_generated_code(code, steps, pattern, knowledge=knowledge)
    print(f"{label}: {'PASSED' if ok else 'FAILED'} {message}")
    if "--show" in sys.argv:
        print(code)
