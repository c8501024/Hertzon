"""What the GUI and the remote service exchange: the job archive (project file + results folder, both
relative to the project's folder, + the files the project reads from elsewhere) and the file lists used to
mirror a results folder."""
from __future__ import annotations

import json
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
JOB_ENV = ("HERTZON_MESH_ONLY", "HERTZON_ONLY_SWEEP", "HERTZON_TUNING", "HERTZON_OPT_ALGORITHM",
           "HERTZON_TUNING_IGNORE_MEMORY")


def safe_rel(path: str) -> PurePosixPath:
    """A relative path inside a job folder; raises ValueError for absolute paths, drives and '..'."""
    p = PurePosixPath(str(path).replace("\\", "/"))
    if not str(path) or p.is_absolute() or any(part in ("", "..") or ":" in part for part in p.parts):
        raise ValueError(f"not a relative path inside the job: {path!r}")
    if p.parts and p.parts[0] == ".":
        raise ValueError(f"not a relative path inside the job: {path!r}")
    return p


# folder of the job that holds the files the project reads (imported CAD/STL, Touchstone, Verilog-A, SPICE)
EXTERNAL = "_external"


class MissingFiles(FileNotFoundError):
    """Files the solved design reads that are not on this computer."""


def _external_refs(data: dict):
    """(props, key, design name, used) of every property of the project data that names files: Import
    operations, Touchstone files of lumped components and the model files of circuit parts. USED: the solve
    of that design reads it (not a suppressed operation, a lumped component with the Touchstone model)."""
    for d in data.get("designs", []):
        name = d.get("name", "")
        for o in d.get("objects", []):
            for c in o.get("history", []):
                if c.get("kind") == "Import" and c.get("props", {}).get("File", "").strip():
                    yield c["props"], "File", name, not c.get("suppressed", False)
        for a in list(d.get("boundaries", [])) + list(d.get("excitations", [])):
            props = a.get("props", {})
            if props.get("TouchstoneFile", "").strip():
                yield props, "TouchstoneFile", name, props.get("Model") == "Touchstone"
        for c in d.get("components", []):
            if c.get("type") in ("DEVICE", "SPICELIB", "SNP"):
                for key in ("File", "OSDI"):
                    if c.get("props", {}).get(key, "").strip():
                        yield c["props"], key, name, True


def pack(project: Path, out: Path, dest: Path, design: str | None = None) -> str:
    """Write the job archive to DEST: the project file and every file of the results folder OUT, stored
    relative to the project's folder, and the files the project reads from elsewhere under EXTERNAL, with the
    packed project pointing at them. Raises MissingFiles when a file DESIGN reads is not there (files of
    other designs that are missing stay as they are). Returns OUT relative to that folder (posix)."""
    from ..model.design import is_absolute_path
    examples = Path(__file__).resolve().parents[2] / "examples"     # shipped with Hertzon on both computers
    project, out = Path(project), Path(out)
    base = project.parent
    rel_out = out.resolve().relative_to(base.resolve()).as_posix()
    data = json.loads(project.read_text(encoding="utf-8"))
    packed: dict[Path, str] = {}           # local file -> its path in the job
    missing: list[str] = []
    for props, key, name, used in _external_refs(data):
        parts = []
        for text in props[key].split(";") if key in ("File", "OSDI") else [props[key]]:
            t = text.strip().strip('"')
            if not t:
                parts.append(text)
                continue
            local = Path(t) if is_absolute_path(t) else base / t
            if local.is_file():
                local = local.resolve()
                if local not in packed:
                    packed[local] = f"{EXTERNAL}/{len(packed) + 1}_{local.name}"
                parts.append(packed[local])
            else:
                if used and name == design and not (not is_absolute_path(t) and (examples / t).is_file()):
                    missing.append(t)
                parts.append(text)
        props[key] = ";".join(parts)
    if missing:
        raise MissingFiles(f"Design '{design}' reads files that are not on this computer: "
                           + ", ".join(dict.fromkeys(missing)))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr(project.name, json.dumps(data, indent=1))
        for local, arc in packed.items():
            z.write(local, arc)
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
