"""Central mapping of LLM model tags for roles.

Keep roles explicit and small; other modules should import `get_model_for_role`.
"""
from typing import Literal


def get_model_for_role(role: str) -> str:
    mapping = {
        "planner": "qwen2.5-coder:1.5b",
        "synthesizer": "qwen2.5-coder:7b",
        "fix_it": "qwen2.5-coder:7b",
    }
    return mapping.get(role, "qwen2.5-coder:7b")
