"""
Inject a sys.meta_path hook into the NeuroBagel API container so that
the /imaging_modalities endpoint merges our custom nb: terms with the
standard nidm: terms fetched from GitHub.

This script is run once at container startup by neuropoly_api_entrypoint.sh,
before uvicorn launches.

Why sys.meta_path instead of a direct import:
  sitecustomize.py runs before uvicorn adds the app to sys.path, so
  `import app.api.utility` at that point raises ModuleNotFoundError.
  A meta_path finder defers the patch until the moment FastAPI actually
  imports the module, by which time sys.path is fully configured.
"""

import site
import sys
from pathlib import Path

PATCH = r'''
import sys
import json
from pathlib import Path

_VOCAB_PATH = Path("/usr/src/neurobagel/neuropoly_imaging_modalities.json")


class _NeuropolyVocabFinder:
    """Intercepts the import of app.api.utility and patches request_data."""

    def find_module(self, name, path=None):
        if name == "app.api.utility":
            return self
        return None

    def load_module(self, name):
        if name in sys.modules:
            return sys.modules[name]
        # Remove ourselves before importing to avoid infinite recursion.
        sys.meta_path.remove(self)
        import importlib
        mod = importlib.import_module(name)
        _orig = mod.request_data

        def _patched(url, err):
            if "imaging_modalities.json" in url and _VOCAB_PATH.exists():
                try:
                    base = _orig(url, err)
                    custom = json.loads(_VOCAB_PATH.read_text())
                    if isinstance(base, dict):
                        base = [base]
                    result = base + custom
                    print(
                        f"[neuropoly-vocab] patch applied: {len(result)} namespace blocks, "
                        f"{sum(len(ns.get('terms', [])) for ns in result)} total terms",
                        file=sys.stderr,
                        flush=True,
                    )
                    return result
                except Exception as exc:
                    print(f"[neuropoly-vocab] patch error: {exc}", file=sys.stderr, flush=True)
            return _orig(url, err)

        mod.request_data = _patched
        sys.modules[name] = mod
        return mod


sys.meta_path.insert(0, _NeuropolyVocabFinder())
'''

# Find site-packages, with virtualenv fallback.
try:
    packages = site.getsitepackages()
except AttributeError:
    packages = [site.getusersitepackages()]

_OLD_SENTINEL = "import app.api.utility as _util"  # identifies the v1 broken patch
# unique to the v2 meta_path hook
_NEW_SENTINEL = "_NeuropolyVocabFinder"

if not packages:
    print("[neuropoly-vocab] Could not find site-packages; patch not installed", file=sys.stderr)
else:
    sc_path = Path(packages[0]) / "sitecustomize.py"
    existing = sc_path.read_text() if sc_path.exists() else ""
    if _NEW_SENTINEL in existing:
        print(f"[neuropoly-vocab] patch already present in {sc_path}")
    elif _OLD_SENTINEL in existing:
        # Remove the old broken patch and write the new meta_path hook.
        sc_path.write_text(PATCH.strip() + "\n")
        print(
            f"[neuropoly-vocab] old patch replaced with meta_path hook in {sc_path}")
    else:
        sc_path.write_text(existing + "\n" + PATCH)
        print(f"[neuropoly-vocab] meta_path hook written to {sc_path}")
