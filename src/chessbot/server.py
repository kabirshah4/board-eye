"""Serves the phone app and runs the game over one WebSocket.

Phone → server: binary JPEG frames (sent when the board goes still) and JSON
commands (`new_game`, `corners`, `undo`, `set_move`). Server → phone: JSON
events (`state`, `best_move`, `opponent_move`, `reaim`, `capture`, `log`).
"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import chess
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .board_reader import LocalReader
from .engine import Engine
from .game import Game, describe
from .vision import Jev, JevError, Reader

load_dotenv()

# With a reader that assumes my move (Jev), a still frame this soon after a
# suggestion is the opponent's hand leaving, not my move.
MY_MOVE_GRACE = 0.6
WEB = Path(__file__).parent / "web"


def make_reader() -> Reader:
    """BOARD_READER=jev reads moves with Jev (paid credits); anything else reads locally."""
    key = os.environ.get("JEV_API_KEY")
    if os.environ.get("BOARD_READER") == "jev" and key:
        return Jev(key, os.environ.get("JEV_IMAGE_FORMAT", "blocks"))
    return LocalReader()


class Session:
    """One game, one phone. Frames are handled one at a time, newest first."""

    def __init__(self, engine: Engine, reader: Reader) -> None:
        self.engine = engine
        self.reader = reader
        self.game = Game()
        self.ws: WebSocket | None = None
        self.suggested_at = 0.0
        self.has_corners = False
        self.needs_reference = False  # the next frame is the board as it now stands
        self._pending: bytes | None = None
        self._worker: asyncio.Task | None = None

    async def send(self, event: dict) -> None:
        if self.ws:
            try:
                await self.ws.send_json(event)
            except Exception:
                pass

    async def log(self, line: str) -> None:
        await self.send({"type": "log", "line": f"[{datetime.now():%H:%M:%S}] {line}"})

    async def state(self) -> None:
        await self.send({"type": "state", **self.game.snapshot()})

    async def relock(self) -> None:
        """Ask the phone for a photo of the board as it now stands, to measure the next move against."""
        if self.reader.needs_corners and self.has_corners:
            self.needs_reference = True
            await self.send({"type": "capture"})

    async def new_game(self, color: str) -> None:
        self.engine.cancel()
        self.game = Game(chess.WHITE if color == "white" else chess.BLACK)
        await self.log(f"game started, playing {color}")
        await self.state()
        if self.game.is_my_turn:
            await self.suggest(started=time.perf_counter(), read_ms=0)
        else:
            self.engine.precompute(self.game.board)

    async def suggest(self, started: float, read_ms: float) -> None:
        reply = await self.engine.best(self.game.board)
        label = describe(self.game.board, reply.move)
        self.game.suggested = reply.move
        self.suggested_at = time.monotonic()
        total = (time.perf_counter() - started) * 1000
        await self.log(f"query stockfish: {reply.source} ({reply.think * 1000:.0f}ms search, {reply.ms:.0f}ms)")
        await self.send({
            "type": "best_move", "uci": label.uci, "text": label.text, "speech": label.speech,
            "mate": label.is_mate, "timing": {"read_ms": round(read_ms), "engine_ms": round(reply.ms), "server_ms": round(total)},
        })
        await self.log(f"new move sent: {label.log}")
        await self.state()

    async def apply_mine(self, move: chess.Move) -> None:
        if self.game.suggested and move != self.game.suggested:
            await self.log(f"you played {describe(self.game.board, move).log}, not the suggestion")
        label = self.game.push(move)
        await self.log(f"player move confirmed: {label.log}")
        await self.state()
        if not self.game.is_over:
            self.engine.precompute(self.game.board)

    async def apply_opponent(self, move: chess.Move, started: float, read_ms: float) -> None:
        label = self.game.push(move)
        await self.send({"type": "opponent_move", "uci": label.uci, "text": label.text})
        await self.log(f"opponent turn finished: {label.log}")
        if self.game.is_over:
            await self.state()
        else:
            await self.suggest(started, read_ms)

    def frame(self, jpeg: bytes) -> None:
        self._pending = jpeg
        if not self._worker or self._worker.done():
            self._worker = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending is not None:
            jpeg, self._pending = self._pending, None
            try:
                await self.handle_frame(jpeg)
            except JevError as e:
                await self.send({"type": "reaim", "reason": str(e)})
                await self.log(f"jev error: {e}")
            except Exception as e:
                await self.log(f"error: {e!r}")

    async def handle_frame(self, jpeg: bytes) -> None:
        if self.reader.needs_corners and not self.has_corners:
            await self.send({"type": "reaim", "reason": "tap the board's four corners"})
            return
        if self.needs_reference:
            self.reader.commit(jpeg)
            self.needs_reference = False
            await self.log("board locked in")
            return
        if self.game.is_over:
            return
        started = time.perf_counter()
        if self.game.is_my_turn and not self.reader.reads_own_moves:
            if self.game.suggested and time.monotonic() - self.suggested_at > MY_MOVE_GRACE:
                await self.apply_mine(self.game.suggested)
            return
        read = await self.reader.which_move(jpeg, self.game.board)
        await self.log(f"board read: {read.move.uci() if read.move else 'no move'} @ {read.confidence:.2f} ({read.ms:.0f}ms)")
        if read.move is None:
            return
        if read.confidence < self.reader.min_confidence or read.move not in self.game.board.legal_moves:
            await self.send({"type": "reaim", "reason": f"unsure ({read.confidence:.2f})"})
            return
        self.reader.commit(jpeg)
        if self.game.is_my_turn:
            await self.apply_mine(read.move)
        else:
            await self.apply_opponent(read.move, started, read.ms)

    async def command(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "new_game":
            await self.new_game(msg.get("color", "white"))
            await self.relock()
        elif kind == "corners":
            self.reader.set_corners(msg["points"], self.game.my_color)
            self.has_corners = True
            await self.log("corners set")
            await self.relock()
        elif kind == "undo":
            undone = self.game.undo()
            await self.log(f"undo: {undone.uci() if undone else 'nothing to undo'}")
            await self.after_correction()
        elif kind == "set_move":
            try:
                move = self.game.parse(msg.get("move", ""))
            except ValueError as e:
                await self.send({"type": "reaim", "reason": str(e)})
                return
            mine = self.game.is_my_turn
            label = self.game.push(move)
            await self.log(f"{'player' if mine else 'opponent'} move set by hand: {label.log}")
            await self.after_correction()
        elif kind == "hello":
            await self.state()

    async def after_correction(self) -> None:
        """Pick the game back up from wherever a correction left it."""
        await self.relock()
        await self.state()
        if self.game.is_over:
            return
        if self.game.is_my_turn:
            await self.suggest(time.perf_counter(), 0)
        else:
            self.engine.precompute(self.game.board)


def create_app(engine: Engine | None = None, reader: Reader | None = None) -> FastAPI:
    holder: dict[str, Session] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = engine or Engine()
        await eng.start()
        rdr = reader or make_reader()
        holder["session"] = Session(eng, rdr)
        yield
        await eng.stop()
        if isinstance(rdr, Jev):
            await rdr.close()

    app = FastAPI(lifespan=lifespan)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        session = holder["session"]
        session.ws = ws  # the newest connection wins; a reconnect takes over
        await session.state()
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    session.frame(msg["bytes"])
                elif msg.get("text"):
                    await session.command(json.loads(msg["text"]))
        except WebSocketDisconnect:
            pass
        finally:
            if session.ws is ws:
                session.ws = None

    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


app = create_app()
