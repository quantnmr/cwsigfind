#!/usr/bin/env python3
"""Regenerate docs/lib/prefixes.js from src/cwsigfind/geo.py.

The lite browser demo (under docs/) is a pure static page with no build step,
so we can't import the Python sources at request time. Instead, this script
serializes the four lookup tables that live in `geo.py` — the prefix → country
table and the human-readable country/US-state/CA-province tables — into a
single ES module that the browser can `import`.

Run from the repo root any time geo.py changes:

    python3 scripts/gen_prefixes.py

It writes to docs/lib/prefixes.js and is idempotent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cwsigfind.geo import (  # noqa: E402
    CA_PROVINCES,
    COUNTRY_NAMES,
    US_STATES,
    _PREFIX_TO_COUNTRY,
)

OUT = ROOT / "docs" / "lib" / "prefixes.js"

HEADER = """// Auto-generated from src/cwsigfind/geo.py by scripts/gen_prefixes.py.
// Do not edit by hand — re-run the generator instead so the browser demo
// and the Python daemon stay in lock-step.
"""


def dump(name: str, table: dict[str, str]) -> str:
    return f"export const {name} = " + json.dumps(table, indent=2, sort_keys=True, ensure_ascii=False) + ";\n"


def main() -> None:
    body = HEADER + "\n"
    body += dump("PREFIX_TO_COUNTRY", _PREFIX_TO_COUNTRY)
    body += "\n"
    body += dump("COUNTRY_NAMES", COUNTRY_NAMES)
    body += "\n"
    body += dump("US_STATES", US_STATES)
    body += "\n"
    body += dump("CA_PROVINCES", CA_PROVINCES)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(_PREFIX_TO_COUNTRY)} prefixes, "
          f"{len(COUNTRY_NAMES)} countries)")


if __name__ == "__main__":
    main()
