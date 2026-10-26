# Third-party software

Hertzon itself is licensed under the GNU GPL v2 or later (see `LICENSE`). It uses the packages below.
None of them is shipped inside the Hertzon installers: `setup.ps1` (Windows) and `install.sh` (Linux)
download them from the Python Package Index (PyPI) into Hertzon's own Python environment (`.venv`)
on the user's computer. Each package keeps its own licence, which `pip show --verbose <name>` prints.

Versions are those of a current installation; `pyproject.toml` lists the requirements.

## Used directly by Hertzon

| Package | Version | Licence | Used for |
|---|---|---|---|
| [EMerge](https://github.com/FennisRobert/EMerge) | 3.0.0b5 + `hertzon` branch at [`debe7ba`](https://github.com/FennisRobert/EMerge/commit/debe7bab11c26369d5955b8afd8cda1e14e513ca) | GPL-2.0-or-later | FEM solver (its compiled core `emr` is part of the package); installed from wheels built from that commit (release `emerge-debe7ba` of this repository) |
| [PySide6](https://pyside.org) (with shiboken6, PySide6 Essentials/Addons) | 6.12.0 | LGPL-3.0 / GPL-2.0 / GPL-3.0 | Qt user interface |
| [PyVista](https://github.com/pyvista/pyvista) | 0.49.1 | MIT | 3D display |
| [pyvistaqt](https://github.com/pyvista/pyvistaqt) | 0.13.1 | MIT | 3D view inside the Qt window |
| [VTK](https://vtk.org) | 9.7.1 | BSD-3-Clause | 3D rendering (through PyVista) |
| [gmsh](https://gmsh.info) | 4.15.2 | GPL-2.0-or-later | Geometry (OpenCASCADE) and meshing (through EMerge) |
| [Matplotlib](https://matplotlib.org) | 3.11.2 | Matplotlib licence (PSF-based) | Reports and plots |
| [NumPy](https://numpy.org) | 2.2.6 | BSD-3-Clause | Numerics |
| [SciPy](https://scipy.org) | 1.18.1 | BSD-3-Clause | Numerics, sparse solvers |
| [Numba](https://numba.pydata.org) (with llvmlite) | 0.68.0 | BSD-2-Clause (llvmlite: BSD-2-Clause and Apache-2.0 with LLVM exception) | Hertzon's compiled kernels (curved elements, refinement size field) |
| [pymmg](https://www.mmgtools.org/) | 1.0.0 | MIT wrapper, Mmg library LGPL-3.0-or-later | "Mmg" adaptive refinement |
| [fast-simplification](https://github.com/pyvista/fast-simplification) | 0.2.0 | MIT | STL simplification on import |
| [hfsynpy](https://github.com/GenerativeAntennaDesign/hfsynpy) | 0.1.3 | GPL-2.0-or-later | Microstrip synthesis in the filter and antenna design tools |
| [pytest](https://pytest.org) (tests only) | | MIT | Test suite |

## Pulled in by the packages above

| Package | Version | Licence | Needed by |
|---|---|---|---|
| emsutil | 1.1.4 | GPL-2.0-or-later (material database `lib.py`: CC0) | EMerge; its material database is offered as the "EMerge Library" in Hertzon's material dialog |
| joblib | 1.6.0 | BSD-3-Clause | EMerge |
| loguru | 0.7.3 | MIT | EMerge |
| msgpack | 1.2.3 | Apache-2.0 | EMerge |
| msgpack-numpy | 0.4.8 | BSD | EMerge |
| psutil | 7.2.2 | BSD-3-Clause | EMerge |
| cloudpickle | 3.1.2 | BSD-3-Clause | joblib |
| mkl, tbb, tcmlib, intel-openmp, intel-cmplr-lib-ur, umf | 2026.2.0 | Intel Simplified Software License (free to use and redistribute, not open source) | EMerge (PARDISO solver) |
| meshio | 5.3.5 | MIT | pymmg |
| rich, markdown-it-py, mdurl, Pygments | | MIT / BSD-2-Clause | meshio |
| contourpy, cycler, kiwisolver, fonttools, pillow, pyparsing, python-dateutil, six, packaging | | BSD / MIT / MIT-CMU / Apache-2.0 | Matplotlib |
| pooch, scooby, cyclopts, attrs, docstring_parser, rich-rst, pyvista-validation, typing_extensions | | BSD-3-Clause / MIT / Apache-2.0 / PSF-2.0 | PyVista |
| QtPy | 2.4.3 | MIT | pyvistaqt |
| requests, urllib3, idna, charset-normalizer, certifi, platformdirs | | Apache-2.0 / MIT / BSD-3-Clause / MPL-2.0 | pooch |

## Code included in this repository

| File | Origin | Licence |
|---|---|---|
| `hertzon/mesher/src/predicates.c` | Robust geometric predicates by Jonathan Richard Shewchuk | Public domain |
| `hertzon/gui/formula/mathlive.min.js`, `hertzon/gui/formula/fonts/` | [MathLive](https://github.com/arnog/mathlive) 0.109.2 by Arno Gourdol (with the KaTeX fonts it ships), the formula editor of the Fields Calculator; licence text in `hertzon/gui/formula/LICENSE-mathlive.txt` | MIT (fonts: SIL OFL 1.1) |

## Notes

* Hertzon is GPL-2.0-or-later, which matches EMerge, gmsh and hfsynpy. Anyone who passes Hertzon on
  must pass on its source code as well; the GitHub repository is that source.
* Intel MKL and its companions are not open source. They are installed by pip as a dependency of
  EMerge and are not part of the Hertzon installers.
