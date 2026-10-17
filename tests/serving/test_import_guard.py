"""Import guard: subprocess proves no forbidden imports in serve path (spec 17.2).

Runs a subprocess that installs a MetaPathFinder raising ImportError on numpy, pandas, sklearn,
lightgbm, matplotlib, scipy, orjson, uvloop, httptools; then imports ``chainwatch.serving.app``,
builds an app with the committed mini models (``tests/fixtures/mini_models``, cfg from
``tests/tiny.py``), scores ten events via ``TestClient`` and reads ``/v1/metrics``. Proves no
forbidden import happens.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = r"""
import sys
from pathlib import Path

# ensure repo on sys.path
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))

class Blocker:
    BLOCK = {"numpy", "pandas", "sklearn", "scikit_learn", "lightgbm",
             "matplotlib", "scipy", "orjson", "uvloop", "httptools"}
    def find_spec(self, fullname, path, target=None):
        pkg = fullname.split('.')[0]
        if pkg in self.BLOCK:
            # ModuleNotFoundError (not the broader ImportError) so libraries that
            # optionally probe for these deps inside try/except ModuleNotFoundError
            # keep working while a real import still fails
            raise ModuleNotFoundError(f"blocked in serve test: {fullname}")
        return None

sys.meta_path.insert(0, Blocker())

from fastapi.testclient import TestClient
from chainwatch.serving.app import create_app
from tests.tiny import MINI_MODELS_DIR, tiny_cfg

cfg = tiny_cfg()
app = create_app(model_dir=MINI_MODELS_DIR, replay=False, cfg=cfg)
with TestClient(app) as c:
    # score a handful of events
    clock = max(c.app.state.cw.engine.features.clock, 0.0)
    for i in range(10):
        ev = {
            "event_id": 9000 + i,
            "ts": clock + 5.0 + i,
            "type": "TRANSFER",
            "src": f"A00500{i:02d}",
            "dst": f"A00501{i:02d}",
            "amount": 1000.0 + i,
        }
        r = c.post("/v1/score", json=ev)
        assert r.status_code == 200, r.text
    m = c.get("/v1/metrics").json()
    assert "latency_us" in m and "total" in m["latency_us"]
print("ok")
"""


def test_import_guard_passes_in_subprocess(tmp_path):
    # write the script
    script = tmp_path / "import_guard.py"
    script.write_text(_SCRIPT)
    repo_root = Path(__file__).resolve().parents[2]  # .../tests/serving -> up two -> repo root
    result = subprocess.run(
        [sys.executable, str(script), str(repo_root)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout

    # and the forbidden modules really were absent from the process
    assert "blocked in serve test" not in result.stderr
