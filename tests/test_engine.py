import shutil

import chess
import pytest

from chessbot.engine import Engine, key

pytestmark = pytest.mark.skipif(not shutil.which("stockfish"), reason="stockfish not installed")


@pytest.fixture
async def engine():
    e = Engine(passes=(0.01, 0.03), live_seconds=0.05)
    await e.start()
    yield e
    await e.stop()


async def test_precompute_covers_every_opponent_move(engine):
    board = chess.Board()
    board.push_san("e4")  # black to move: 20 replies
    await engine.precompute(board)
    for move in board.legal_moves:
        after = board.copy()
        after.push(move)
        assert engine.cache[key(after)][1] == 0.03
        reply = await engine.best(after)
        assert reply.source == "precomputed"
        assert reply.move in after.legal_moves
        assert reply.ms < 5


async def test_live_search_finds_mate_in_one(engine):
    # White to move: Qxf7#
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    reply = await engine.best(board)
    assert reply.source == "live"
    assert reply.move == chess.Move.from_uci("h5f7")


async def test_cancel_stops_precompute(engine):
    task = engine.precompute(chess.Board())
    engine.cancel()
    with pytest.raises(BaseException):
        await task
    # the engine still answers afterwards
    reply = await engine.best(chess.Board())
    assert reply.move in chess.Board().legal_moves
