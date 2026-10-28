__version__ = "0.2.6"

import os as _os
import sys as _sys

# Hertzon.exe starts the GUI with pythonw, which has no console: sys.stdout and sys.stderr are None.
# EMerge installs its log handler on sys.stdout at import, and loguru refuses a None sink ("Cannot log
# to objects of type 'NoneType'"), which broke the first field plot after a start.
for _name in ("stdout", "stderr"):
    if getattr(_sys, _name) is None:
        setattr(_sys, _name, open(_os.devnull, "w", encoding="utf-8"))
