from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "app").is_dir():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from app.foundry_config import FoundrySettings, FoundrySettingsStore

SMOKE_SECRET = "container-smoke-api-key"


def settings() -> FoundrySettings:
    return FoundrySettings.model_validate({
        "schemaVersion": 3,
        "endpointKind": "model",
        "endpoint": "https://smoke.services.ai.azure.com",
        "auth": {"type": "api_key", "apiKey": SMOKE_SECRET},
        "agentInstructions": "Container encryption smoke test.",
        "apiProfiles": [{
            "apiType": "responses",
            "models": ["smoke-deployment"],
            "defaultModel": "smoke-deployment",
            "versionMode": "v1",
            "options": {"store": False},
        }],
        "defaultSelection": {"apiType": "responses", "model": "smoke-deployment"},
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("write", "read"))
    parser.add_argument("--path", type=Path, default=Path("/data/FoundrySettings.json"))
    args = parser.parse_args()
    store = FoundrySettingsStore(args.path)

    if args.mode == "write":
        store.save(settings())
        persisted = args.path.read_text(encoding="utf-8")
        if SMOKE_SECRET in persisted or '"apiKey"' in persisted:
            raise RuntimeError("Container settings contain a plaintext API key.")
        print("container encrypted settings write OK")
        return 0

    loaded = store.load()
    if loaded is None or loaded.api_key != SMOKE_SECRET:
        raise RuntimeError("Container encrypted settings roundtrip failed.")
    print("container encrypted settings read OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
