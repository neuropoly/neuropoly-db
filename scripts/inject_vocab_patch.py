"""
Inject a sitecustomize.py patch into the NeuroBagel API container so that
the /imaging_modalities endpoint merges our custom nb: terms with the
standard nidm: terms fetched from GitHub.

This script is run once at container startup by neuropoly_api_entrypoint.sh,
before uvicorn launches.
"""

import site
from pathlib import Path

PATCH = '''
import json
from pathlib import Path
try:
    import app.api.utility as _util
    _orig = _util.request_data
    def _patched(url, err):
        if "imaging_modalities.json" in url:
            p = Path("/usr/src/neurobagel/neuropoly_imaging_modalities.json")
            if p.exists():
                base_data = _orig(url, err)  # still fetch standard terms
                custom = json.loads(p.read_text())
                return base_data + custom
        return _orig(url, err)
    _util.request_data = _patched
except Exception:
    pass
'''

sc_path = Path(site.getsitepackages()[0]) / "sitecustomize.py"
existing = sc_path.read_text() if sc_path.exists() else ""
if "neuropoly_imaging_modalities" not in existing:
    sc_path.write_text(existing + "\n" + PATCH)
