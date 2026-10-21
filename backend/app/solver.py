"""Exact shortest-path solver (half-turn metric) using bidirectional breadth-first search.

The search is complete: it expands the scrambled state and the solved state one full
BFS layer at a time, always growing the smaller frontier, and stops when the frontiers
meet. The first meeting depth is the exact optimum under the 18-move HTM metric used by
the game engine. For the primary benchmark (4-5 move scrambles) this finishes in well
under a second; ``max_depth`` caps the work for long extension scrambles, where the
result is reported as "unknown" instead of guessing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .cube import INVERSE, MOVES, SOLVED, apply_move, is_solved

# Moves allowed after a move on face X: skip same face (merges) and, for opposite faces,
# only allow one canonical order. This prunes duplicates without losing optimal paths.
_ORDER = {f: i for i, f in enumerate("URFDLB")}
_AXIS = {"U": 0, "D": 0, "R": 1, "L": 1, "F": 2, "B": 2}


def _successor_moves(last: str | None) -> tuple[str, ...]:
    if last is None:
        return MOVES
    lf = last[0]
    out = []
    for m in MOVES:
        f = m[0]
        if f == lf:
            continue
        if _AXIS[f] == _AXIS[lf] and _ORDER[f] < _ORDER[lf]:
            continue
        out.append(m)
    return tuple(out)


_SUCC = {None: _successor_moves(None), **{m: _successor_moves(m) for m in MOVES}}


@dataclass
class SolveResult:
    distance: int | None          # exact optimal move count, None if > max_depth
    solution: list[str] = field(default_factory=list)
    exact: bool = True
    max_depth: int = 0
    nodes: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "distance": self.distance,
            "solution": self.solution,
            "exact": self.exact,
            "max_depth": self.max_depth,
            "nodes": self.nodes,
            "seconds": round(self.seconds, 4),
        }


def _expand(frontier, parents, other_parents):
    """Expand one BFS layer. Returns (new_frontier, meeting_state or None)."""
    new_frontier = {}
    meet = None
    for state, last in frontier.items():
        for m in _SUCC[last]:
            nxt = apply_move(state, m)
            if nxt in parents:
                continue
            parents[nxt] = (state, m)
            new_frontier[nxt] = m
            if meet is None and nxt in other_parents:
                meet = nxt
    return new_frontier, meet


def _path_from(parents, state):
    path = []
    while True:
        prev = parents[state]
        if prev is None:
            break
        state, m = prev
        path.append(m)
    path.reverse()
    return path


def solve_optimal(start: str, max_depth: int = 10, target: str = SOLVED) -> SolveResult:
    """Exact shortest solution from ``start`` to ``target`` (default: solved) up to ``max_depth``."""
    t0 = time.perf_counter()
    if start == target or (target == SOLVED and is_solved(start)):
        return SolveResult(0, [], True, max_depth, 1, time.perf_counter() - t0)

    fwd_parents = {start: None}
    bwd_parents = {target: None}
    fwd_frontier = {start: None}
    bwd_frontier = {target: None}
    d_fwd = d_bwd = 0

    while d_fwd + d_bwd < max_depth:
        if len(fwd_frontier) <= len(bwd_frontier):
            fwd_frontier, meet = _expand(fwd_frontier, fwd_parents, bwd_parents)
            d_fwd += 1
        else:
            bwd_frontier, meet = _expand(bwd_frontier, bwd_parents, fwd_parents)
            d_bwd += 1
        if meet is not None:
            # Forward half: start -> meet. Backward half was built from target with moves m,
            # so walking target -> meet; invert it to go meet -> target.
            first = _path_from(fwd_parents, meet)
            second_rev = _path_from(bwd_parents, meet)
            solution = first + [INVERSE[m] for m in reversed(second_rev)]
            return SolveResult(
                len(solution), solution, True, max_depth,
                len(fwd_parents) + len(bwd_parents), time.perf_counter() - t0,
            )
        if not fwd_frontier or not bwd_frontier:  # pragma: no cover - cannot happen on a cube
            break

    return SolveResult(
        None, [], False, max_depth, len(fwd_parents) + len(bwd_parents), time.perf_counter() - t0
    )


def best_next_moves(state: str, max_depth: int = 8) -> tuple[int | None, list[str]]:
    """All moves that reduce the exact distance by one (used by the offline simulator)."""
    base = solve_optimal(state, max_depth)
    if base.distance is None or base.distance == 0:
        return base.distance, []
    good = []
    for m in MOVES:
        r = solve_optimal(apply_move(state, m), base.distance - 1)
        if r.distance is not None and r.distance == base.distance - 1:
            good.append(m)
    return base.distance, good
