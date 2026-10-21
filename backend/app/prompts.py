"""The single game-action contract shared by every model.

Both interfaces (Decisions API and chat completions) receive the *same* state object,
the *same* instruction text and the *same* 18 move definitions. Only the transport
differs, so the comparison stays fair.

Following OpenRouter's Decisions guidance, the state contains only fields the question
reads, and the instructions reference those fields by name.
"""

from __future__ import annotations

import json

from .cube import FACES, MOVE_DESCRIPTIONS, faces_dict

QUESTION_ID = "next_move"

ENCODING = (
    "`cube_facelets` has 54 letters in URFDLB order: Up 0-8, Right 9-17, Front 18-26, Down 27-35, "
    "Left 36-44, Back 45-53. Each face is read row by row as seen looking straight at it "
    "(Up seen from above with Back at the top; Down seen from below with Front at the top; "
    "side faces with Up at the top). A letter names the face whose centre has that colour, "
    "so a face is solved when all 9 of its letters equal its own face letter. `faces` shows the "
    "same data as 3 rows per face. Moves use Singmaster notation: X = turn face X 90 degrees "
    "clockwise as seen looking at that face, X' = 90 degrees counterclockwise, X2 = 180 degrees."
)

INSTRUCTIONS = (
    "You are solving a 3x3 Rubik's Cube. Read the cube in `cube_facelets` / `faces` (encoding "
    "explained in `encoding`). Choose the single next move that brings the cube closest to solved, "
    "where solved means every face shows only its own letter. The cube may be only a few moves from "
    "solved, so look for the move that directly undoes the scramble. Do not choose the inverse of the "
    "last move in `recent_moves` (that would undo your own progress). Fewer total moves is better."
)


def build_state(facelets: str, history: list[str], move_number: int, max_moves: int,
                history_window: int) -> dict:
    """Identical state payload for every model (only fields the question reads)."""
    recent = history[-history_window:] if history_window > 0 else []
    return {
        "cube_facelets": facelets,
        "faces": {f: "/".join(rows) for f, rows in faces_dict(facelets).items()},
        "encoding": ENCODING,
        "recent_moves": recent,
        "moves_remaining": max_moves - move_number,
    }


def decisions_questions() -> dict:
    return {
        QUESTION_ID: {
            "type": "choice",
            "instructions": INSTRUCTIONS,
            "criteria": dict(MOVE_DESCRIPTIONS),
        }
    }


MOVE_JSON_SCHEMA = {
    "type": "object",
    "properties": {"move": {"type": "string", "enum": list(MOVE_DESCRIPTIONS)}},
    "required": ["move"],
    "additionalProperties": False,
}


def chat_messages(state: dict) -> list[dict]:
    options = "\n".join(f'- "{m}": {d}' for m, d in MOVE_DESCRIPTIONS.items())
    system = (
        f"{INSTRUCTIONS}\n\n"
        f"Allowed moves (choose exactly one):\n{options}\n\n"
        'Respond with ONLY a JSON object of the form {"move": "<one allowed move>"} '
        "and nothing else."
    )
    user = "Current game state:\n" + json.dumps(state, indent=2)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = [
    "FACES", "QUESTION_ID", "INSTRUCTIONS", "MOVE_JSON_SCHEMA",
    "build_state", "decisions_questions", "chat_messages",
]
