import random

from app.cube import MOVES, SOLVED, apply_move, apply_moves, random_scramble
from app.solver import best_next_moves, solve_optimal


def brute_force_distance(state, limit):
    """Plain BFS with no pruning — slow but obviously correct."""
    if state == SOLVED:
        return 0
    seen = {state}
    frontier = [state]
    for d in range(1, limit + 1):
        nxt = []
        for s in frontier:
            for m in MOVES:
                t = apply_move(s, m)
                if t == SOLVED:
                    return d
                if t not in seen:
                    seen.add(t)
                    nxt.append(t)
        frontier = nxt
    return None


def test_matches_brute_force_on_short_scrambles():
    rng = random.Random(3)
    for _ in range(40):
        seq = [rng.choice(MOVES) for _ in range(rng.randint(1, 4))]  # unconstrained: may cancel
        st = apply_moves(SOLVED, seq)
        r = solve_optimal(st, 6)
        assert r.distance == brute_force_distance(st, 4)
        assert apply_moves(st, r.solution) == SOLVED
        assert len(r.solution) == r.distance


def test_primary_scrambles_exact_and_bounded():
    rng = random.Random(11)
    for _ in range(30):
        sc = random_scramble(rng.randint(4, 5), rng)
        st = apply_moves(SOLVED, sc)
        r = solve_optimal(st, len(sc))
        assert r.exact and r.distance is not None and 1 <= r.distance <= len(sc)
        assert apply_moves(st, r.solution) == SOLVED


def test_seven_move_case():
    rng = random.Random(5)
    sc = random_scramble(7, rng)
    st = apply_moves(SOLVED, sc)
    r = solve_optimal(st, 8)
    assert r.distance is not None and r.distance <= 7
    assert apply_moves(st, r.solution) == SOLVED


def test_depth_cap_reports_unknown():
    st = apply_moves(SOLVED, random_scramble(18, random.Random(2)))
    r = solve_optimal(st, 4)
    assert r.distance is None and not r.exact


def test_solved_state():
    assert solve_optimal(SOLVED).distance == 0


def test_best_next_moves():
    st = apply_moves(SOLVED, ["R", "U"])
    d, good = best_next_moves(st, 4)
    assert d == 2 and good == ["U'"]
