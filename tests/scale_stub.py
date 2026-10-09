from flask import request


def stable_scale_snapshot():
    """Supply a stable hardware reading for tests of unrelated capture workflows."""
    return {"scale_connected": True, "weight_stable": True,
            "weight": str((request.get_json(silent=True) or {}).get("weight", ""))}
