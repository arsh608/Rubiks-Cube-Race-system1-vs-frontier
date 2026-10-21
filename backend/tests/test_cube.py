import random

import pytest

from app.cube import (INVERSE, MOVES, SOLVED, InvalidMoveError, apply_move, apply_moves, faces_dict,
                      invert_sequence, is_solved, parse_moves, random_scramble, validate_state)


def faces(s):
    return [s[i * 9:(i + 1) * 9] for i in range(6)]


def test_reference_single_moves():
    # Reference facelet strings (Kociemba URFDLB convention).
    assert faces(apply_move(SOLVED, "R")) == ["UUFUUFUUF", "RRRRRRRRR", "FFDFFDFFD", "DDBDDBDDB", "LLLLLLLLL", "UBBUBBUBB"]
    assert faces(apply_move(SOLVED, "U")) == ["UUUUUUUUU", "BBBRRRRRR", "RRRFFFFFF", "DDDDDDDDD", "FFFLLLLLL", "LLLBBBBBB"]
    assert faces(apply_move(SOLVED, "F")) == ["UUUUUULLL", "URRURRURR", "FFFFFFFFF", "RRRDDDDDD", "LLDLLDLLD", "BBBBBBBBB"]
    assert faces(apply_move(SOLVED, "D")) == ["UUUUUUUUU", "RRRRRRFFF", "FFFFFFLLL", "DDDDDDDDD", "LLLLLLBBB", "BBBBBBRRR"]
    assert faces(apply_move(SOLVED, "L")) == ["BUUBUUBUU", "RRRRRRRRR", "UFFUFFUFF", "FDDFDDFDD", "LLLLLLLLL", "BBDBBDBBD"]
    assert faces(apply_move(SOLVED, "B")) == ["RRRUUUUUU", "RRDRRDRRD", "FFFFFFFFF", "DDDDDDLLL", "ULLULLULL", "BBBBBBBBB"]


@pytest.mark.parametrize("m", MOVES)
def test_move_and_inverse(m):
    s = apply_move(SOLVED, m)
    assert s != SOLVED
    assert apply_move(s, INVERSE[m]) == SOLVED
    validate_state(s)


@pytest.mark.parametrize("f", "URFDLB")
def test_quarter_turn_order_four(f):
    s = SOLVED
    for _ in range(4):
        s = apply_move(s, f)
    assert s == SOLVED
    assert apply_moves(SOLVED, [f, f]) == apply_move(SOLVED, f + "2")


def test_sexy_move_order_six():
    s = SOLVED
    for i in range(6):
        s = apply_moves(s, ["R", "U", "R'", "U'"])
        assert (s == SOLVED) == (i == 5)


def test_random_sequences_invert():
    rng = random.Random(7)
    for _ in range(200):
        seq = [rng.choice(MOVES) for _ in range(rng.randint(1, 25))]
        s = apply_moves(SOLVED, seq)
        validate_state(s)
        assert apply_moves(s, invert_sequence(seq)) == SOLVED


def test_scramble_properties():
    rng = random.Random(1)
    for n in range(1, 21):
        sc = random_scramble(n, rng)
        assert len(sc) == n
        assert all(a[0] != b[0] for a, b in zip(sc, sc[1:]))
        assert not is_solved(apply_moves(SOLVED, sc))


def test_scramble_is_deterministic_for_seed():
    assert random_scramble(5, random.Random(42)) == random_scramble(5, random.Random(42))


def test_invalid_move_rejected():
    with pytest.raises(InvalidMoveError):
        apply_move(SOLVED, "X")
    with pytest.raises(InvalidMoveError):
        parse_moves("R U Q")
    assert parse_moves("R, U' F2") == ["R", "U'", "F2"]


def test_faces_dict_and_solved():
    assert faces_dict(SOLVED)["F"] == ["FFF", "FFF", "FFF"]
    assert is_solved(SOLVED)
    assert not is_solved(apply_move(SOLVED, "R2"))
