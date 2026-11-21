import shutil

import chess
import pytest
from fastapi.testclient import TestClient

from chessbot import server
from chessbot.engine import Engine
from chessbot.board_reader import LocalReader
from chessbot.vision import JevError, Read
from render import WHITE_SEAT, photo

pytestmark = pytest.mark.skipif(not shutil.which("stockfish"), reason="stockfish not installed")


class FakeJev:
    """Answers `which_move` from a script, one entry per frame, like Jev: opponent moves only."""

    reads_own_moves = False
    needs_corners = False
    min_confidence = 0.8

    def set_corners(self, points, my_color):
        pass

    def commit(self, jpeg):
        pass

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def which_move(self, jpeg, board):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        uci, confidence = item
        return Read(chess.Move.from_uci(uci) if uci else None, confidence, 12.0)

    async def close(self):
        pass


def until(ws, kind, where=lambda event: True):
    while True:
        event = ws.receive_json()
        if event["type"] == kind and where(event):
            return event


@pytest.fixture(autouse=True)
def no_grace(monkeypatch):
    monkeypatch.setattr(server, "MY_MOVE_GRACE", 0)


def client(script):
    jev = FakeJev(script)
    app = server.create_app(engine=Engine(passes=(0.01,), live_seconds=0.05), reader=jev)
    return TestClient(app), jev


def test_full_loop_as_white():
    tc, jev = client([("e7e5", 0.95)])
    with tc, tc.websocket_connect("/ws") as ws:
        ws.send_json({"type": "new_game", "color": "white"})
        first = until(ws, "best_move")
        assert first["uci"] in [m.uci() for m in chess.Board().legal_moves]

        ws.send_bytes(b"my move frame")  # my turn: the suggestion is applied, no Jev call
        state = until(ws, "state")
        while state["my_turn"]:
            state = until(ws, "state")
        assert jev.calls == 0

        ws.send_bytes(b"opponent frame")  # e7e5 is legal after any first move
        assert until(ws, "opponent_move")["uci"] == "e7e5"
        reply = until(ws, "best_move")
        assert set(reply["timing"]) == {"read_ms", "engine_ms", "server_ms"}
        assert jev.calls == 1


def test_unsure_read_asks_to_reaim_and_none_is_ignored():
    tc, jev = client([("e2e4", 0.4), (None, 0.99), ("e2e4", 0.9)])
    with tc, tc.websocket_connect("/ws") as ws:
        ws.send_json({"type": "new_game", "color": "black"})
        until(ws, "state")
        ws.send_bytes(b"f1")
        assert "unsure" in until(ws, "reaim")["reason"]
        ws.send_bytes(b"f2")  # "none": nothing happens
        ws.send_bytes(b"f3")
        assert until(ws, "opponent_move")["uci"] == "e2e4"
        until(ws, "best_move")
        assert jev.calls == 3


def test_jev_failure_and_manual_fix():
    tc, jev = client([JevError("Jev timed out")])
    with tc, tc.websocket_connect("/ws") as ws:
        ws.send_json({"type": "new_game", "color": "black"})
        until(ws, "state")
        ws.send_bytes(b"frame")
        assert until(ws, "reaim")["reason"] == "Jev timed out"
        ws.send_json({"type": "set_move", "move": "d4"})
        reply = until(ws, "best_move")
        board = chess.Board()
        board.push_san("d4")
        assert chess.Move.from_uci(reply["uci"]) in board.legal_moves
        ws.send_json({"type": "undo"})
        until(ws, "log", lambda e: "undo: d2d4" in e["line"])
        state = until(ws, "state")
        assert state["moves"] == 0 and not state["my_turn"]


def test_local_reader_plays_a_game_from_photos():
    """Real image processing end to end: corners, lock-in, my move checked, the opponent's read."""
    app = server.create_app(engine=Engine(passes=(0.01,), live_seconds=0.05), reader=LocalReader())
    board = chess.Board()
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ws.send_bytes(photo(board))
        assert "corners" in until(ws, "reaim")["reason"]

        ws.send_json({"type": "new_game", "color": "white"})
        mine = chess.Move.from_uci(until(ws, "best_move")["uci"])
        ws.send_json({"type": "corners", "points": [list(p) for p in WHITE_SEAT]})
        until(ws, "capture")
        ws.send_bytes(photo(board, seed=1))
        until(ws, "log", lambda e: "board locked in" in e["line"])

        # I play something other than the suggestion: the reader sees what I really played.
        played = next(m for m in board.legal_moves if m != mine and m.uci() in ("e2e4", "d2d4"))
        board.push(played)
        ws.send_bytes(photo(board, seed=2))
        until(ws, "log", lambda e: "player move confirmed" in e["line"])

        reply = "e7e5" if played.uci() == "d2d4" else "c7c5"
        board.push_uci(reply)
        ws.send_bytes(photo(board, seed=3))
        assert until(ws, "opponent_move")["uci"] == reply
        suggestion = until(ws, "best_move")
        assert chess.Move.from_uci(suggestion["uci"]) in board.legal_moves
