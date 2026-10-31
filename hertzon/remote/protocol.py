"""What the GUI and the remote service exchange: the job archive (project file + results folder, both
relative to the project's folder) and the file lists used to mirror a results folder."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path, PurePosixPath

# solver entry points a job may start; nothing else is ever run by the service
KINDS = {
    "run": "hertzon.solver.run",
    "parametric": "hertzon.solver.parametric",
    "optimize": "hertzon.solver.optimize",
}
# environment of the solver process the GUI may set per job (cores and workers are the service's own)
JOB_ENV = ("HERTZON_MESH_ONLY", "HERTZON_ONLY_SWEEP", "HERTZON_TUNING")


def safe_rel(path: str) -> PurePosixPath:
    """A relative path inside a job folder; raises ValueError for absolute paths, drives and '..'."""
    p = PurePosixPath(str(path).replace("\\", "/"))
    if not str(path) or p.is_absolute() or any(part in ("", "..") or ":" in part for part in p.parts):
        raise ValueError(f"not a relative path inside the job: {path!r}")
    if p.parts and p.parts[0] == ".":
        raise ValueError(f"not a relative path inside the job: {path!r}")
    return p


def pack(project: Path, out: Path, dest: Path) -> str:
    """Write the job archive to DEST: the project file and every file of the results folder OUT, stored
    relative to the project's folder. Returns OUT relative to that folder (posix)."""
    project, out = Path(project), Path(out)
    base = project.parent
    rel_out = out.resolve().relative_to(base.resolve()).as_posix()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.write(project, project.name)
        if out.is_dir():
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    z.write(f, (PurePosixPath(rel_out) / f.relative_to(out).as_posix()).as_posix())
    return rel_out


def unpack(archive: Path, dest: Path) -> None:
    """Extract a job archive into DEST, refusing any entry that would land outside it."""
    dest = Path(dest)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            rel = safe_rel(info.filename.rstrip("/"))
            target = dest.joinpath(*rel.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)


def file_list(folder: Path) -> list[dict]:
    """Files below FOLDER: relative posix path, size and modification time (ns)."""
    folder = Path(folder)
    files = []
    if folder.is_dir():
        for f in folder.rglob("*"):
            if f.is_file():
                st = f.stat()
                files.append({"path": f.relative_to(folder).as_posix(), "size": st.st_size,
                              "mtime": st.st_mtime_ns})
    files.sort(key=lambda e: e["path"])
    return files


def changed(remote: list[dict], seen: dict[str, tuple[int, int]]) -> list[dict]:
    """Entries of REMOTE whose (size, mtime) differs from what was fetched before (SEEN), smallest first,
    so the small result files (S-parameters, convergence) arrive before the large field files."""
    out = [e for e in remote if seen.get(e["path"]) != (e["size"], e["mtime"])]
    out.sort(key=lambda e: (e["size"], e["path"]))
    return out


def local_target(folder: Path, rel: str) -> Path:
    p = safe_rel(rel)
    return Path(folder).joinpath(*p.parts)


def replace_file(tmp: Path, target: Path) -> None:
    """Move a downloaded file into place (Windows: a reader may hold the old one for a moment)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(20):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            import time
            time.sleep(0.1)
