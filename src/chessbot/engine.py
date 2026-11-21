"""Stockfish, with my reply to every opponent move worked out before it is played.

Two engine processes: `precompute` walks every legal opponent move in passes of
rising think time while the opponent thinks, and `live` stays free for the rare
position the cache has not reached, so a lookup never waits behind a search.
"""

import asyncio
import shutil
import time
from dataclasses import dataclass

import chess
import chess.engine

PASSES = (0.03, 0.15, 0.5)  # seconds per position, per pass


def key(board: chess.Board) -> str:
    return board.epd()


@dataclass(frozen=True)
class Reply:
    move: chess.Move
    think: float  # seconds of search behind it
    source: str  # "precomputed" or "live"
    ms: float  # time spent answering this lookup


class Engine:
    def __init__(self, path: str | None = None, live_seconds: float = 0.2, passes: tuple[float, ...] = PASSES) -> None:
        self.path = path or shutil.which("stockfish") or "/opt/homebrew/bin/stockfish"
        self.live_seconds = live_seconds
        self.passes = passes
        self.cache: dict[str, tuple[chess.Move, float]] = {}
        self._live: chess.engine.UciProtocol | None = None
        self._pre: chess.engine.UciProtocol | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        _, self._live = await chess.engine.popen_uci(self.path)
        _, self._pre = await chess.engine.popen_uci(self.path)

    async def stop(self) -> None:
        self.cancel()
        for proc in (self._live, self._pre):
            if proc:
                try:
                    await asyncio.wait_for(proc.quit(), 2)
                except Exception:
                    pass

    async def best(self, board: chess.Board) -> Reply:
        started = time.perf_counter()
        hit = self.cache.get(key(board))
        if hit:
            move, think = hit
            return Reply(move, think, "precomputed", (time.perf_counter() - started) * 1000)
        assert self._live, "engine not started"
        result = await self._live.play(board, chess.engine.Limit(time=self.live_seconds))
        assert result.move
        return Reply(result.move, self.live_seconds, "live", (time.perf_counter() - started) * 1000)

    def precompute(self, board: chess.Board) -> asyncio.Task:
        """Start finding my reply to each legal move from `board` (the opponent to move)."""
        self.cancel()
        self._task = asyncio.create_task(self._precompute(board.copy()))
        return self._task

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _precompute(self, board: chess.Board) -> None:
        assert self._pre, "engine not started"
        replies = []
        for move in board.legal_moves:
            after = board.copy(stack=False)
            after.push(move)
            if not after.is_game_over():
                replies.append(after)
        for seconds in self.passes:
            for after in replies:
                k = key(after)
                if k in self.cache and self.cache[k][1] >= seconds:
                    continue
                result = await self._pre.play(after, chess.engine.Limit(time=seconds))
                if result.move:
                    self.cache[k] = (result.move, seconds)
