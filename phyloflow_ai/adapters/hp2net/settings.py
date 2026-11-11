from ...config import MODEL_NAME, OLLAMA_API_ENDPOINT
from .adapter import ADAPTER


ORIGINAL_KB_PATH = ADAPTER.original_knowledge
TARGET_KB_PATH = ADAPTER.target_knowledge
WORKFLOW_SOURCE_PATH = ADAPTER.workflow_source

IMPLEMENTATION_SPEC = {
    "project_name": "HP2NET",
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
        "Do not emit optional arguments merely because they exist in the app signature. Emit an argument only when the implementation pattern requires it or when the original HP2Net workflow uses it.",
        "The function signature must be exactly (config, basedir, prepare_to_run).",
        "The function must return a list of futures."
    ],
    "few_shot_example": '''
EXPECTED SHAPE OF THE OUTPUT (structure only, follow the numbered steps for the real content):

def <function_name>(config, basedir, prepare_to_run):
    ...
    return [<final_future>]
'''
    "ret_raxml = []"
    "for input_file in glob.glob(os.path.join(basedir['dir'], 'input', 'phylip', '*.phy')):"
    "ret_raxml.append(apps.raxml(basedir=basedir, config=config, inputs=prepare_to_run, input_file=input_file))"
    "ret_tree = apps.setup_tree_output(basedir=basedir, config=config, inputs=ret_raxml)"
    "ret_ast = apps.astral(basedir=basedir, config=config, inputs=[ret_tree])"
    "return [ret_ast]"
}
