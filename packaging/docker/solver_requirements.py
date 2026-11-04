"""Print the requirements of the headless solver image: the dependencies of pyproject.toml without the GUI
packages (the remote service and the solver never import them)."""
import sys
import tomllib

GUI = ("pyside6", "pyvistaqt")      # EMerge itself needs pyvista and matplotlib

deps = tomllib.load(open(sys.argv[1], "rb"))["project"]["dependencies"]
for d in deps:
    name = d.split("@")[0].split(">")[0].split("<")[0].split("=")[0].split(";")[0].strip().lower()
    if name not in GUI:
        print(d)
