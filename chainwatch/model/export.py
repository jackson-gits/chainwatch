"""Export a LightGBM booster to the portable JSON format ``chainwatch-gbdt-1`` (train env, spec 10.3)."""

from __future__ import annotations

import json
from pathlib import Path

from chainwatch.model.portable_gbdt import FORMAT


class ExportError(ValueError):
    pass


def _tree_arrays(structure: dict) -> dict:
    """Flatten one LightGBM ``tree_structure`` into node arrays indexed by LightGBM's own split/leaf
    indices."""
    if "split_index" not in structure:
        return {
            "feature": [],
            "threshold": [],
            "left": [],
            "right": [],
            "leaf": [float(structure["leaf_value"])],
            "root_leaf": True,
        }
    nodes: dict[int, tuple] = {}
    leaves: dict[int, float] = {}
    stack = [structure]
    while stack:
        node = stack.pop()
        if "split_index" in node:
            if node.get("decision_type") != "<=":
                raise ExportError(
                    f"unsupported decision_type {node.get('decision_type')!r} (categorical split?)"
                )
            if node.get("missing_type", "None") not in ("None", None):
                raise ExportError(
                    f"unsupported missing_type {node.get('missing_type')!r}; train with use_missing=false"
                )
            if isinstance(node.get("threshold"), str):
                raise ExportError("categorical threshold found; categorical splits are not supported")

            def child_ref(ch: dict) -> int:
                if "split_index" in ch:
                    return int(ch["split_index"])
                return ~int(ch.get("leaf_index", 0))

            nodes[int(node["split_index"])] = (
                int(node["split_feature"]),
                float(node["threshold"]),
                child_ref(node["left_child"]),
                child_ref(node["right_child"]),
            )
            stack.append(node["left_child"])
            stack.append(node["right_child"])
        else:
            leaves[int(node.get("leaf_index", 0))] = float(node["leaf_value"])
    n_int = len(nodes)
    if sorted(nodes) != list(range(n_int)) or sorted(leaves) != list(range(n_int + 1)):
        raise ExportError("unexpected node or leaf numbering in the LightGBM dump")
    return {
        "feature": [nodes[i][0] for i in range(n_int)],
        "threshold": [nodes[i][1] for i in range(n_int)],
        "left": [nodes[i][2] for i in range(n_int)],
        "right": [nodes[i][3] for i in range(n_int)],
        "leaf": [leaves[j] for j in range(n_int + 1)],
    }


def booster_to_portable(
    booster, detector: str, feature_names: list[str], registry_hash: str, num_iteration: int, trained_on: dict
) -> dict:
    dump = booster.dump_model(num_iteration=num_iteration)
    if dump.get("objective", "").split(" ")[0] != "binary":
        raise ExportError(f"only binary objective is supported, got {dump.get('objective')!r}")
    sigmoid = 1.0
    for part in dump.get("objective", "").split(" ")[1:]:
        if part.startswith("sigmoid:"):
            sigmoid = float(part.split(":", 1)[1])
    names_in_model = dump.get("feature_names", [])
    if names_in_model and list(names_in_model) != list(feature_names):
        raise ExportError("booster feature names differ from the registry names")
    trees = [_tree_arrays(t["tree_structure"]) for t in dump["tree_info"]]
    return {
        "format": FORMAT,
        "detector": detector,
        "feature_names": list(feature_names),
        "registry_hash": registry_hash,
        "objective": "binary",
        "sigmoid": sigmoid,
        "trained_on": trained_on,
        "n_trees": len(trees),
        "trees": trees,
    }


def write_model(model: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(model, fh, separators=(",", ":"))
