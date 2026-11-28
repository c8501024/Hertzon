"""Writes functions/_lib/suite.js and public/suite.json from the Hertzon Bench definition
(hertzon/solver/suite.py and examples/benchmark/suite/reference.json). Run after changing the suite:

    python bench-site/make_suite_spec.py

Only the current suite version is kept: results of older versions are not shown or accepted.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from hertzon.solver.suite import site_spec  # noqa: E402

path = HERE / "public" / "suite.json"
spec = site_spec()
text = json.dumps(spec, indent=1)
path.write_text(text + "\n", encoding="utf-8")
(HERE / "functions" / "_lib" / "suite.js").write_text(
    "// Written by make_suite_spec.py from the Hertzon Bench definition; do not edit.\n"
    f"export const SUITES = {text};\n", encoding="utf-8")
print("Suites:", ", ".join(spec))
