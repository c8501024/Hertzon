"""Small helpers to draw a schematic from code: parts are placed, then the pins are joined by named nets."""
from __future__ import annotations

from .. import circuit as CK
from ..circuit import Component as C


class Schematic:
    """Collects parts and the nets their pins belong to; wires() joins every net (see filters._join)."""

    def __init__(self):
        self.parts: list[C] = []
        self.nets: dict[str, list[tuple[int, int]]] = {}

    def add(self, name: str, ctype: str, x: int, y: int, rot: int = 0, mirror: bool = False, **props) -> C:
        c = C(name, ctype, x, y, rot, {k: str(v) for k, v in props.items()}, mirror)
        self.parts.append(c)
        return c

    def pin(self, c: C, which: str | int) -> tuple[int, int]:
        """A pin by index (0 based) or by side: 'left', 'right', 'top', 'bottom' (the schematic's y grows down)."""
        pts = CK.pins(c)
        if isinstance(which, int):
            return pts[which]
        key = {"left": lambda p: p[0], "right": lambda p: -p[0], "top": lambda p: p[1], "bottom": lambda p: -p[1]}
        return min(pts, key=key[which])

    def net(self, name: str, *pins: tuple[int, int]) -> None:
        self.nets.setdefault(name, []).extend(pins)

    def port(self, num: int, z0: str, at: tuple[int, int], side: str) -> C:
        """A power port whose pin sits on 'at', drawn below it and a little to the side ('left' / 'right')."""
        x, y = at
        dx = -40 if side == "left" else 40
        c = self.add(f"Port{num}", "PORT", x + dx, y + 40, Num=num, Z0=z0)
        self.net(f"P{num}", at, self.pin(c, 0))
        return c

    def build(self, design) -> None:
        from ..filters import _join
        design.components = list(self.parts)
        design.wires = _join(design.components, self.nets)
