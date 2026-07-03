import ast
from pathlib import Path
from typing import List


GPU_IMPORTS = {"torch", "tensorflow", "jax", "cupy", "paddle"}
LLM_IMPORTS = {"openai", "anthropic", "cohere", "mistralai", "google.generativeai"}
PARALLEL_IMPORTS = {"multiprocessing", "joblib", "ray", "dask", "concurrent"}
NETWORK_CALLS = {"requests", "urllib", "httpx", "wget", "boto3", "huggingface_hub"}


def analyze_repo(repo_path: Path) -> dict:
    imports = set()
    has_cuda_calls = False
    has_network_calls = False

    for py_file in Path(repo_path).rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(errors="ignore"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            # collect all of the imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])

            # look for .cuda() or .to("cuda") calls
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "cuda":
                        has_cuda_calls = True
                    if node.func.attr == "to":
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and "cuda" in str(arg.value):
                                has_cuda_calls = True

    # check network usage
    has_network_calls = bool(imports & NETWORK_CALLS)

    return {
        "needs_gpu": bool(imports & GPU_IMPORTS) or has_cuda_calls,
        "external_llm": next((i for i in LLM_IMPORTS if i in imports), None),
        "parallelizable": bool(imports & PARALLEL_IMPORTS),
        "requires_data_download": has_network_calls,
        "key_dependencies": sorted(imports - {"os", "sys", "json", "re", "math",
                                               "typing", "pathlib", "datetime"}),
    }