"""Deterministic 3x3 Rubik's Cube engine.

State is a 54-character facelet string in the standard Kociemba / URFDLB order:

    U1..U9, R1..R9, F1..F9, D1..D9, L1..L9, B1..B9

Each face is read row-major as seen when looking straight at that face, using the
usual unfolded-net orientation (U with B on top, D with F on top, side faces with
U on top). Each character is the letter of the face whose *centre* has that colour,
so the solved cube is "U"*9 + "R"*9 + "F"*9 + "D"*9 + "L"*9 + "B"*9.

Move permutations are derived from 3-D geometry (each sticker has a position and
a normal vector, a face turn is a 90 degree rotation of one layer) instead of hand
typed tables, which removes a whole class of transcription bugs. Every model in the
benchmark uses exactly this engine and these move definitions.
"""

from __future__ import annotations

import random
from operator import itemgetter
from typing import Iterable

FACES = "URFDLB"
SOLVED = "".join(f * 9 for f in FACES)

# Outward normal of each face: x = right, y = up, z = front.
_NORMALS = {
    "U": (0, 1, 0),
    "R": (1, 0, 0),
    "F": (0, 0, 1),
    "D": (0, -1, 0),
    "L": (-1, 0, 0),
    "B": (0, 0, -1),
}

MOVE_DESCRIPTIONS = {
    "U": "Turn the upper face clockwise by 90 degrees",
    "U'": "Turn the upper face counterclockwise by 90 degrees",
    "U2": "Turn the upper face by 180 degrees",
    "R": "Turn the right face clockwise by 90 degrees",
    "R'": "Turn the right face counterclockwise by 90 degrees",
    "R2": "Turn the right face by 180 degrees",
    "F": "Turn the front face clockwise by 90 degrees",
    "F'": "Turn the front face counterclockwise by 90 degrees",
    "F2": "Turn the front face by 180 degrees",
    "D": "Turn the down (bottom) face clockwise by 90 degrees",
    "D'": "Turn the down (bottom) face counterclockwise by 90 degrees",
    "D2": "Turn the down (bottom) face by 180 degrees",
    "L": "Turn the left face clockwise by 90 degrees",
    "L'": "Turn the left face counterclockwise by 90 degrees",
    "L2": "Turn the left face by 180 degrees",
    "B": "Turn the back face clockwise by 90 degrees",
    "B'": "Turn the back face counterclockwise by 90 degrees",
    "B2": "Turn the back face by 180 degrees",
}
MOVES: tuple[str, ...] = tuple(MOVE_DESCRIPTIONS)  # 18 moves, half-turn metric


def _sticker_geometry(face: str, idx: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Return (position, normal) of sticker ``idx`` (0..8) on ``face``."""
    r, c = divmod(idx, 3)
    if face == "U":
        pos = (c - 1, 1, r - 1)
    elif face == "D":
        pos = (c - 1, -1, 1 - r)
    elif face == "F":
        pos = (c - 1, 1 - r, 1)
    elif face == "B":
        pos = (1 - c, 1 - r, -1)
    elif face == "R":
        pos = (1, 1 - r, 1 - c)
    elif face == "L":
        pos = (-1, 1 - r, c - 1)
    else:  # pragma: no cover
        raise ValueError(face)
    return pos, _NORMALS[face]


_GEOMETRY = [_sticker_geometry(f, i) for f in FACES for i in range(9)]
_INDEX = {g: n for n, g in enumerate(_GEOMETRY)}
assert len(_INDEX) == 54


def _rotate_cw(v, axis):
    """Rotate vector ``v`` 90 degrees clockwise when viewed from the tip of ``axis``.

    Clockwise seen from outside == -90 degrees by the right-hand rule:
    R(v) = -(a x v) + a (a . v)
    """
    ax, ay, az = axis
    vx, vy, vz = v
    cx = ay * vz - az * vy
    cy = az * vx - ax * vz
    cz = ax * vy - ay * vx
    d = ax * vx + ay * vy + az * vz
    return (-cx + ax * d, -cy + ay * d, -cz + az * d)


def _quarter_turn_perm(face: str) -> tuple[int, ...]:
    """perm such that new_state[i] = old_state[perm[i]] for a clockwise quarter turn."""
    axis = _NORMALS[face]
    perm = list(range(54))
    for src, (pos, normal) in enumerate(_GEOMETRY):
        if sum(p * a for p, a in zip(pos, axis)) == 1:  # sticker is in the turning layer
            dst = _INDEX[(_rotate_cw(pos, axis), _rotate_cw(normal, axis))]
            perm[dst] = src
    return tuple(perm)


def _compose(p: tuple[int, ...], q: tuple[int, ...]) -> tuple[int, ...]:
    """Permutation equal to applying p then q."""
    return tuple(p[q[i]] for i in range(54))


PERMS: dict[str, tuple[int, ...]] = {}
for _f in FACES:
    _q = _quarter_turn_perm(_f)
    _h = _compose(_q, _q)
    PERMS[_f] = _q
    PERMS[_f + "2"] = _h
    PERMS[_f + "'"] = _compose(_h, _q)
_GETTERS = {m: itemgetter(*p) for m, p in PERMS.items()}

INVERSE = {m: (m[0] if m.endswith("'") else m if m.endswith("2") else m + "'") for m in MOVES}
# Opposite faces commute; used to avoid redundant sequences such as "R L R".
OPPOSITE = {"U": "D", "D": "U", "R": "L", "L": "R", "F": "B", "B": "F"}


class InvalidMoveError(ValueError):
    pass


def apply_move(state: str, move: str) -> str:
    getter = _GETTERS.get(move)
    if getter is None:
        raise InvalidMoveError(f"Unknown move {move!r}; allowed: {', '.join(MOVES)}")
    return "".join(getter(state))


def apply_moves(state: str, moves: Iterable[str]) -> str:
    for m in moves:
        state = apply_move(state, m)
    return state


def parse_moves(text: str) -> list[str]:
    moves = text.replace(",", " ").split()
    for m in moves:
        if m not in PERMS:
            raise InvalidMoveError(f"Unknown move {m!r}")
    return moves


def invert_sequence(moves: Iterable[str]) -> list[str]:
    return [INVERSE[m] for m in reversed(list(moves))]


def is_solved(state: str) -> bool:
    """True when every face shows a single colour (orientation independent)."""
    return all(len(set(state[i * 9:(i + 1) * 9])) == 1 for i in range(6))


def validate_state(state: str) -> None:
    if len(state) != 54:
        raise ValueError("state must have 54 facelets")
    for f in FACES:
        if state.count(f) != 9:
            raise ValueError(f"state must contain exactly nine {f!r} facelets")
    if tuple(state[4 + 9 * i] for i in range(6)) != tuple(FACES):
        raise ValueError("centre facelets must be U,R,F,D,L,B")


def faces_dict(state: str) -> dict[str, list[str]]:
    """{'U': ['UUU','UUU','UUU'], ...} — row-major rows for each face."""
    return {
        f: [state[i * 9 + r * 3: i * 9 + r * 3 + 3] for r in range(3)]
        for i, f in enumerate(FACES)
    }


def allowed_next(prev: str | None, prev2: str | None = None) -> list[str]:
    """Moves that do not trivially cancel / merge with the previous ones (scramble generation)."""
    out = []
    for m in MOVES:
        f = m[0]
        if prev is not None:
            if f == prev[0]:
                continue
            # Canonical order for commuting opposite faces avoids "R L R" style sequences.
            if prev2 is not None and OPPOSITE[f] == prev[0] and f == prev2[0]:
                continue
        out.append(m)
    return out


def random_scramble(length: int, rng: random.Random) -> list[str]:
    """Random scramble of ``length`` moves with no same-face repeats or A-B-A on one axis.

    Guaranteed to never return a sequence that leaves the cube solved.
    """
    if length < 1:
        raise ValueError("scramble length must be >= 1")
    while True:
        seq: list[str] = []
        for _ in range(length):
            prev = seq[-1] if seq else None
            prev2 = seq[-2] if len(seq) > 1 else None
            seq.append(rng.choice(allowed_next(prev, prev2)))
        if not is_solved(apply_moves(SOLVED, seq)):
            return seq
