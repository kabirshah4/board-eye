"""The only code that talks to Jev. Jev reports what it sees; game.py decides.

Jev answers typed questions, so the opponent's move is a `choice` whose only
options are the legal moves plus "none": it cannot name an illegal move.
How Jev takes an image is not in its API reference yet, so the shape of
`state` is picked by JEV_IMAGE_FORMAT (scripts/probe_jev.py finds the one that works).
"""

import asyncio
import base64
import os
import time
from dataclasses import dataclass
from typing import Protocol

import chess
import httpx

from .game import describe

DIRECT = "https://api.typesafe.ai"
GATEWAY = "https://ai-gateway.vercel.sh/typesafe"  # Vercel AI Gateway's pass-through
NONE = "none"


def base_url(api_key: str) -> str:
    """JEV_BASE_URL if set, else the gateway for a Vercel key (vck_...), else TypeSafe direct."""
    return os.environ.get("JEV_BASE_URL") or (GATEWAY if api_key.startswith("vck_") else DIRECT)


INSTRUCTIONS = (
    "The text gives the chess position before the opponent's move; the photo shows the "
    "physical board now. Which of these moves has been played on the board? "
    "Answer none if the board still matches the position before, or if a hand is covering it."
)


def build_state(fmt: str, text: str, jpeg: bytes) -> object:
    b64 = base64.b64encode(jpeg).decode()
    data_url = f"data:image/jpeg;base64,{b64}"
    if fmt == "blocks":
        return [
            {"type": "text", "text": text},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        ]
    if fmt == "image_url":
        return [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": data_url}}]
    if fmt == "object":
        return {"position": text, "image": data_url}
    raise ValueError(f"unknown JEV_IMAGE_FORMAT {fmt!r}")


def position_text(board: chess.Board) -> str:
    side = "White" if board.turn == chess.WHITE else "Black"
    return f"{side} to move. FEN: {board.fen()}\n{board}"


@dataclass(frozen=True)
class Read:
    move: chess.Move | None  # None: no move played yet
    confidence: float
    ms: float


class Reader(Protocol):
    """Reads the move just played off a board photo; board_reader.LocalReader and Jev."""

    reads_own_moves: bool  # False: my move is assumed to be the suggestion
    needs_corners: bool  # True: compares against a reference photo of the board
    min_confidence: float

    def set_corners(self, points: list[list[float]], my_color: chess.Color) -> None: ...
    def commit(self, jpeg: bytes) -> None: ...
    async def which_move(self, jpeg: bytes, board: chess.Board) -> Read: ...


class JevError(Exception):
    pass


class Jev:
    reads_own_moves = False  # each read is a paid call, so only the opponent's moves are read
    needs_corners = False

    def __init__(self, api_key: str, image_format: str = "blocks", model: str = "jev-latest", timeout: float = 3.0) -> None:
        self.image_format = image_format
        self.model = model
        self.min_confidence = float(os.environ.get("MIN_CONFIDENCE", "0.8"))
        self.client = httpx.AsyncClient(
            base_url=base_url(api_key),
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "TypeSafe-Danger-Zone-Images": "true"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    def set_corners(self, points: list[list[float]], my_color: chess.Color) -> None:
        pass

    def commit(self, jpeg: bytes) -> None:
        pass

    async def evaluate(self, state: object, questions: dict) -> dict:
        body = {"model": self.model, "state": state, "questions": questions}
        for attempt in range(2):
            try:
                response = await self.client.post("/v1/systemone", json=body)
            except httpx.TimeoutException as e:
                if attempt:
                    raise JevError("Jev timed out") from e
                continue
            if response.status_code in (429, 529) or response.status_code >= 500:
                if attempt:
                    raise JevError(f"Jev HTTP {response.status_code}")
                await asyncio.sleep(0.2)
                continue
            if response.status_code != 200:
                raise JevError(f"Jev HTTP {response.status_code}: {response.text[:200]}")
            return response.json()["answers"]
        raise JevError("unreachable")

    async def which_move(self, jpeg: bytes, board: chess.Board) -> Read:
        started = time.perf_counter()
        options = {m.uci(): f"{'White' if board.turn else 'Black'} {describe(board, m).log}" for m in board.legal_moves}
        options[NONE] = "No move has been played yet; the board matches the position before"
        answers = await self.evaluate(
            build_state(self.image_format, position_text(board), jpeg),
            {"move": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": options}},
        )
        answer = answers["move"]
        choice = answer["choice"]
        move = None if choice == NONE else chess.Move.from_uci(choice)
        return Read(move, float(answer.get("confidence", 0.0)), (time.perf_counter() - started) * 1000)
