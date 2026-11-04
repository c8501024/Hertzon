# Contributing to Hertzon

Thanks for helping make Hertzon better. Bug reports, ideas, example projects, documentation and code are
all welcome.

## Reporting a problem

Open an [issue](https://github.com/c8501024/Hertzon/issues/new) and include:

- the Hertzon version (**Help > About**) and your operating system,
- what you did, what you expected and what happened instead,
- the error message or the log, if there is one,
- if possible, a small `.hzn` project that shows the problem (attach it zipped to the issue).

Simulation results can be wrong. If a result looks suspicious, say what you compared it with (a
measurement, an analytical value or another tool) and how far apart the values are.

## Getting help and suggesting features

Questions about using Hertzon and ideas for new features are welcome in
[Discussions](https://github.com/c8501024/Hertzon/discussions). Start with the
[tutorial](docs/TUTORIAL.md) and the [feature matrix](docs/FEATURE_MATRIX.md), which lists what is
implemented and what is still missing. Planned work is in [docs/ROADMAP.md](docs/ROADMAP.md) and
[BACKLOG.md](BACKLOG.md).

## Contributing code or documentation

1. Fork the repository and create a branch from **`dev`**.
2. Set up a development environment and run the tests (see below).
3. Keep each pull request to one topic, add a test for a bug fix or a new feature where it is possible,
   and add a line to the unreleased section of [CHANGELOG.md](CHANGELOG.md).
4. Open the pull request against **`dev`**, not `main`. `main` holds the last release and is only updated
   by merging `dev` for a new version (see "Branches and releases" in the [README](README.md)).

Every change follows the [design principles](docs/PRINCIPLES.md). In particular, every export (images,
GIFs, plots, reports, data) reproduces exactly what the user sees; any deviation is an explicit user
option, never a default.

### Changes to the solver

The finite-element core is [EMerge](https://github.com/FennisRobert/EMerge) by Robert Fennis. Hertzon
uses EMerge at a pinned commit (`packaging/emerge-commit`) and does not patch it at run time. Bugs in
EMerge itself are best reported or fixed in the EMerge repository; see
[docs/EMERGE_INTEGRATION.md](docs/EMERGE_INTEGRATION.md) for how the two fit together.

## Development setup and tests

Hertzon needs Python 3.10 to 3.13.

```bash
git clone https://github.com/c8501024/Hertzon.git
cd Hertzon
git checkout dev
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[test]"
python -m pytest tests
```

On Linux without a display, run the tests on a virtual display with `xvfb-run -a python -m pytest tests`;
gmsh and VTK also need the OpenGL libraries (`libglu1-mesa`, `libgl1` on Debian and Ubuntu). The workflow
file lists all system packages the tests need. Run `pip install -e ".[test]"` again whenever `packaging/emerge-commit` changes: the tests stop with a message when the installed EMerge is not
the pinned commit.

The tests run automatically on every pull request and every push to `dev` and `main`
([.github/workflows/tests.yml](.github/workflows/tests.yml)).

## Recognition

Everyone who contributes is listed in the [README](README.md#contributors) following the
[all-contributors](https://allcontributors.org) convention.

## License

By contributing you agree that your contribution is licensed under the
[GPL-2.0-or-later](LICENSE), the license of Hertzon.
