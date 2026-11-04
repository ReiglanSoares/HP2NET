IMPLEMENTATION_PATTERNS = {
    ("BUCKy", "Quartet MaxCut"): {
        "apps": ["setup_bucky_data", "bucky", "setup_bucky_output", "setup_qmc_data", "quartet_maxcut", "setup_qmc_output"],
        "multiplicity": {
            "setup_bucky_data": "once", "bucky": "once_per_prune_file",
            "setup_bucky_output": "once_after_bucky", "setup_qmc_data": "once_after_bucky_output",
            "quartet_maxcut": "once", "setup_qmc_output": "once_after_quartet_maxcut"
        },
        "description": (
            "1. Execute apps.setup_bucky_data(basedir, config, inputs=prepare_to_run)\n"
            "2. Wait for setup_bucky_data using wait_for_all([ret_pre_bucky])\n"
            "3. Discover prune trees using glob.glob(os.path.join(basedir['dir'], 'bucky', '*.txt'))\n"
            "4. Execute apps.bucky once per prune file in a loop passing prune_file=prune_tree and inputs=[ret_pre_bucky]\n"
            "5. Execute apps.setup_bucky_output(basedir, config, inputs=ret_bucky)\n"
            "6. Execute apps.setup_qmc_data(basedir, config, inputs=[ret_post_bucky])\n"
            "7. Execute apps.quartet_maxcut(basedir, config, inputs=[ret_pre_qmc])\n"
            "8. Execute apps.setup_qmc_output(basedir, config, inputs=[ret_qmc]) to obtain ret_post_qmc"
        )
    },
    ("BUCKy", "Quartet MaxCut", "SNaQ"): {
        "apps": ["setup_bucky_data", "bucky", "setup_bucky_output", "setup_qmc_data", "quartet_maxcut", "setup_qmc_output", "snaq"],
        "multiplicity": {
            "setup_bucky_data": "once", "bucky": "once_per_prune_file",
            "setup_bucky_output": "once_after_bucky", "setup_qmc_data": "once_after_bucky_output",
            "quartet_maxcut": "once", "setup_qmc_output": "once_after_quartet_maxcut",
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
        "apps": ["raxml", "setup_tree_output", "astral"],
        "multiplicity": {"raxml": "once_per_gene", "setup_tree_output": "once_after_raxml", "astral": "once"},
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
    },
    ("MrBayes", "MBSUM", "BUCKy", "root_tree", "PhyloNet"): {
        "apps": [
            "mrbayes", "mbsum", "setup_bucky_data", "bucky",
            "setup_bucky_output", "setup_tree_output", "root_tree",
            "setup_phylonet_data", "phylonet"
        ],
        "multiplicity": {
            "mrbayes": "once_per_gene",
            "mbsum": "once_per_gene",
            "setup_bucky_data": "once",
            "bucky": "once_per_prune_file",
            "setup_bucky_output": "once_after_bucky",
            "setup_tree_output": "once_after_bucky",
            "root_tree": "once",
            "setup_phylonet_data": "once_per_hmax",
            "phylonet": "once_per_hmax"
        },
        "description": (
            "1. Discover input nexus alignments using datalist = glob.glob(os.path.join(basedir['dir'], 'input', 'nexus', '*.nex'))\n"
            "2. Initialize ret_mbsum = []\n"
            "3. For each input_file in datalist, execute apps.mrbayes(basedir=basedir, config=config, input_file=input_file, inputs=prepare_to_run) to obtain ret_mb\n"
            "4. For the same input_file, execute apps.mbsum(basedir=basedir, config=config, input_file=input_file, inputs=[ret_mb]) and append to ret_mbsum\n"
            "5. Execute apps.setup_bucky_data(basedir=basedir, config=config, inputs=ret_mbsum)\n"
            "6. Wait for setup_bucky_data using wait_for_all([ret_pre_bucky])\n"
            "7. Discover prune trees using glob.glob(os.path.join(basedir['dir'], 'bucky', '*.txt'))\n"
            "8. For each prune_tree, execute apps.bucky(basedir=basedir, config=config, prune_file=prune_tree, inputs=[ret_pre_bucky]) and collect ret_bucky\n"
            "9. Execute apps.setup_bucky_output(basedir=basedir, config=config, inputs=ret_bucky) to obtain ret_post_bucky\n"
            "10. Execute apps.setup_tree_output(basedir=basedir, config=config, inputs=[ret_post_bucky]) to obtain ret_sad\n"
            "11. Execute apps.root_tree(basedir=basedir, config=config, inputs=[ret_sad]) to obtain ret_rooted\n"
            "12. Compute out_dir = os.path.join(basedir['dir'], config.phylonet_dir)\n"
            "13. For each h in config.phylonet_hmax, execute apps.setup_phylonet_data(basedir=basedir, config=config, hmax=h, inputs=[ret_rooted]) to obtain ret_spd, "
            "build filename = os.path.join(out_dir, basedir['tree_method'] + '_' + h + '_' + config.phylonet_input), and execute "
            "apps.phylonet(basedir=basedir, config=config, input_file=filename, inputs=[ret_spd])\n"
            "14. Append each PhyloNet result to result and return result"
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

    lines.append(f"{len(lines) + 1}. Return {previous_variable}.")
    return {
        "apps": apps,
        "multiplicity": {a: app_specs[a].get("cardinality", "once") for a in apps},
        "description": (
            "Generate one Parsl workflow function with the signature "
            "(basedir, config, prepare_to_run). Execute exactly these steps in order, "
            "using the stated variable names and keyword arguments:\n" + "\n".join(lines)
        ),
    }

def resolve_implementation_pattern(steps):
    return IMPLEMENTATION_PATTERNS.get(tuple(steps))
