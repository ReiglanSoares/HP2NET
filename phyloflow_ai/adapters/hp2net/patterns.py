IMPLEMENTATION_PATTERNS = {
    ("MrBayes", "MBSUM", "BUCKy", "Quartet MaxCut"): {
        "apps": ["mrbayes", "mbsum", "setup_bucky_data", "bucky", "setup_bucky_output", "setup_qmc_data", "quartet_maxcut", "setup_qmc_output"],
        "multiplicity": {
            "mrbayes": "once_per_gene", "mbsum": "once_per_gene",
            "setup_bucky_data": "once", "bucky": "once_per_prune_file",
            "setup_bucky_output": "once_after_bucky", "setup_qmc_data": "once_after_bucky_output",
            "quartet_maxcut": "once", "setup_qmc_output": "once_after_quartet_maxcut"
        },
        "description": (
            "1. descobrir os .nex com glob, inicializar ret_mbsum = [], e dentro do laço chamar apps.mrbayes(..., inputs=prepare_to_run) seguido de apps.mbsum(..., inputs=[ret_mb]) com append em ret_mbsum\n"
            "2. Execute apps.setup_bucky_data(basedir, config, inputs=prepare_to_run)\n"
            "3. Wait for setup_bucky_data using wait_for_all([ret_pre_bucky])\n"
            "4. Discover prune trees using glob.glob(os.path.join(basedir['dir'], 'bucky', '*.txt'))\n"
            "5. Execute apps.bucky once per prune file in a loop passing prune_file=prune_tree and inputs=[ret_pre_bucky]\n"
            "6. Execute apps.setup_bucky_output(basedir, config, inputs=ret_bucky)\n"
            "7. Execute apps.setup_qmc_data(basedir, config, inputs=[ret_post_bucky])\n"
            "8. Execute apps.quartet_maxcut(basedir, config, inputs=[ret_pre_qmc])\n"
            "9. Execute apps.setup_qmc_output(basedir, config, inputs=[ret_qmc]) to obtain ret_post_qmc"
            "10. Return [ret_post_qmc] as a list"
        )
    },
    ("RAxML", "ASTRAL"): {
        "apps": ["raxml", "setup_tree_output", "astral"],
        "multiplicity": {"raxml": "once_per_gene", "setup_tree_output": "once_after_raxml", "astral": "once"},
        "description": (
            "1. Discover gene alignments using datalist = glob.glob(os.path.join(basedir['dir'], 'input', 'phylip', '*.phy'))\n"
            "2. Initialize ret_tree = []\n"
            "3. For each input_file in datalist, execute apps.raxml(basedir=basedir, config=config, inputs=prepare_to_run, input_file=input_file) and append the result to ret_tree\n"
            "4. Execute apps.setup_tree_output(basedir=basedir, config=config, inputs=ret_tree) to obtain ret_sad\n"
            "5. Execute apps.astral(basedir=basedir, config=config, inputs=[ret_sad])\n"
            "6. Return the result of apps.astral"
            "7. Return [ret_ast] as a list"
        )
    },
    ("MrBayes", "MBSUM"): {
        "apps": ["mrbayes", "mbsum"],
        "multiplicity": {"mrbayes": "once_per_gene", "mbsum": "once_per_gene"},
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
        "apps": ["raxml", "setup_tree_output", "root_tree", "setup_phylonet_data", "phylonet"],
        "multiplicity": {
            "raxml": "once_per_gene", "setup_tree_output": "once_after_raxml", "root_tree": "once",
            "setup_phylonet_data": "once_per_hmax", "phylonet": "once_per_hmax"
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

def resolve_implementation_pattern(steps):
    return IMPLEMENTATION_PATTERNS.get(tuple(steps))

def build_dynamic_pattern(steps, knowledge):
    tools = knowledge.get("tools", {})
    app_specs = knowledge.get("apps", {})
    apps, lines = [], []
    previous_variable, previous_kind = None, None

    for tool in steps:
        for app in tools[tool]["apps"]:
            if app in apps:
                continue
            spec = app_specs[app]
            variable = f"ret_{app}"
            if previous_variable is None:
                inputs_expression = "prepare_to_run"
            elif previous_kind == "list":
                inputs_expression = previous_variable
            else:
                inputs_expression = f"[{previous_variable}]"
            hint = spec["call_hint"].format(**{"in": inputs_expression, "out": variable})
            lines.append(f"{len(lines) + 1}. {hint}")
            apps.append(app)
            previous_variable = variable
            previous_kind = spec.get("result_kind", "single")

    if previous_kind == "list":
        lines.append(f"{len(lines) + 1}. Return {previous_variable}.")
    else:
        lines.append(f"{len(lines) + 1}. Return [{previous_variable}], a list with the final future.")
    return {
        "apps": apps,
        "multiplicity": {a: app_specs[a].get("cardinality", "once") for a in apps},
        "description": (
            "Generate one Parsl workflow function with the signature "
            "(config, basedir, prepare_to_run), in exactly this parameter order. "
            "Execute exactly these steps in order, "
            "using the stated variable names and keyword arguments:\n" + "\n".join(lines)
        ),
    }

def resolve_implementation_pattern(steps):
    return IMPLEMENTATION_PATTERNS.get(tuple(steps))
