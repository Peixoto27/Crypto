import os
import json
import time
from datetime import datetime
from typing import Any, Dict

import joblib

# gather lightweight package versions without forcing imports
def _get_version(pkg: str):
    try:
        # Python 3.8+ importlib.metadata
        from importlib import metadata
        return metadata.version(pkg)
    except Exception:
        try:
            import pkg_resources
            return pkg_resources.get_distribution(pkg).version
        except Exception:
            return None


def save_model(obj: Any, model_path: str, meta: Dict[str, Any] = None) -> bool:
    """Save a model atomically and write metadata alongside it.

    Args:
        obj: Python object to persist (e.g. joblib-serializable estimator or wrapper).
        model_path: target path for the model (e.g. model.pkl).
        meta: optional dict with metadata to be merged into the saved metadata file.

    Returns:
        True on success, False on failure.
    """
    meta = meta.copy() if isinstance(meta, dict) else {}
    # enrich metadata
    meta.setdefault("saved_at", datetime.utcnow().isoformat() + "Z")
    try:
        meta.setdefault("model_class", type(obj).__name__)
    except Exception:
        meta.setdefault("model_class", str(type(obj)))

    # capture commonly useful package versions (best effort)
    packages = [
        "python", "numpy", "pandas", "scikit-learn", "joblib",
        "lightgbm", "xgboost"
    ]
    versions = {}
    for p in packages:
        if p == "python":
            import sys

            versions[p] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        else:
            v = _get_version(p)
            if v:
                versions[p] = v
    if versions:
        meta.setdefault("package_versions", versions)

    # if repo commit is available in env, add it
    commit = os.getenv("GIT_COMMIT") or os.getenv("CI_COMMIT_SHA") or os.getenv("COMMIT_SHA")
    if commit:
        meta.setdefault("git_commit", commit)

    # atomic write pattern
    model_dir = os.path.dirname(os.path.abspath(model_path)) or "."
    os.makedirs(model_dir, exist_ok=True)
    tmp_model = model_path + ".tmp"
    try:
        # dump model to temporary file
        joblib.dump(obj, tmp_model)
        # replace atomically
        os.replace(tmp_model, model_path)
    except Exception as e:
        # cleanup tmp if any
        try:
            if os.path.exists(tmp_model):
                os.remove(tmp_model)
        except Exception:
            pass
        return False

    # write metadata file next to model
    try:
        meta_path = os.path.splitext(model_path)[0] + "_meta.json"
        with open(meta_path + ".tmp", "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False, indent=2)
        os.replace(meta_path + ".tmp", meta_path)
    except Exception:
        # metadata failure should not break the saved model
        return True

    return True
