"""The models, loaded once on first use. Tests can swap them with `use(m2=..., severity=...)`."""
_loaded = {}


def m2_model():
    if "m2" not in _loaded:
        from backend.ml.m2 import M2
        _loaded["m2"] = M2()
    return _loaded["m2"]


def severity_model():
    if "severity" not in _loaded:
        from backend.ml.severity import Severity
        _loaded["severity"] = Severity()
    return _loaded["severity"]


def use(**models):
    _loaded.update(models)
