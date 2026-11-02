"""Data tables written as CSV text files (Export Data of the reports, Export Matrix Data).

* UTF-8 with a byte order mark, so units such as Ω or ° survive on every platform and spreadsheet
  programs show them as in the plot (the platform default codepage cannot encode them).
* Every header cell is quoted, so names that contain the delimiter, e.g. ``S(1,1)``, look like all others.
* The text is built first and replaces the target file in one step: a failure leaves no empty or
  half-written file behind.
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

ENCODING = "utf-8-sig"


def csv_text(header: Sequence[str], rows: Iterable[Sequence[float]], delimiter: str = ",",
             fmt: str = "%.10g") -> str:
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter, quoting=csv.QUOTE_ALL, lineterminator="\n").writerow(header)
    for r in rows:
        buf.write(delimiter.join(fmt % float(np.real(v)) for v in r) + "\n")
    return buf.getvalue()


def write_text_atomic(path: str | os.PathLike, text: str, encoding: str | None = None) -> None:
    """Write ``text`` to ``path``; on any error the previous file (or no file) stays as it was."""
    path = Path(path)
    data = text.encode(encoding or ENCODING)            # an encoding error is raised before anything is touched
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_csv(path: str | os.PathLike, header: Sequence[str], rows: Iterable[Sequence[float]],
              delimiter: str = ",", fmt: str = "%.10g") -> None:
    write_text_atomic(path, csv_text(header, rows, delimiter, fmt))
