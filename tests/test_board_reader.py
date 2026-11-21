import chess
import pytest

from chessbot.board_reader import LocalReader, touched
from render import WHITE_SEAT, photo


def reader_at(board: chess.Board, seat: chess.Color = chess.WHITE) -> LocalReader:
    reader = LocalReader()
    reader.set_corners([list(p) for p in WHITE_SEAT], seat)
    reader.commit(photo(board, seat))
    return reader


def play(board: chess.Board, *sans: str) -> chess.Board:
    board = board.copy()
    for san in sans:
        board.push_san(san)
    return board


@pytest.mark.parametrize(
    "before, san",
    [
        ([], "e4"),
        (["e4"], "Nf6"),
        (["e4", "d5"], "exd5"),  # capture
        (["e4", "e5"], "Be2"),  # white piece, cream square to cream square: the faintest change
        (["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], "O-O"),  # castling: 4 squares
        (["e4", "a6", "e5", "d5"], "exd6"),  # en passant: 3 squares
    ],
)
async def test_reads_the_move_played(before, san):
    board = play(chess.Board(), *before)
    reader = reader_at(board)
    after = play(board, san)
    read = await reader.which_move(photo(after, seed=1), board)
    assert read.move == board.parse_san(san)
    assert read.confidence >= reader.min_confidence
    assert read.ms < 50


async def test_seen_from_blacks_side():
    board = play(chess.Board(), "e4")
    reader = reader_at(board, chess.BLACK)
    read = await reader.which_move(photo(play(board, "c5"), chess.BLACK, seed=2), board)
    assert read.move == board.parse_san("c5")


async def test_no_move_and_a_lighting_change_read_as_none():
    board = chess.Board()
    reader = reader_at(board)
    assert (await reader.which_move(photo(board, seed=3), board)).move is None
    assert (await reader.which_move(photo(board, light=0.8, seed=4), board)).move is None


async def test_reads_still_work_under_dimmer_light():
    board = play(chess.Board(), "d4")
    reader = reader_at(board)
    read = await reader.which_move(photo(play(board, "d5"), light=0.85, seed=5), board)
    assert read.move == board.parse_san("d5")


def test_touched_squares():
    board = play(chess.Board(), "e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5")
    assert touched(board, board.parse_san("O-O")) == {chess.E1, chess.G1, chess.H1, chess.F1}
    board = play(chess.Board(), "e4", "a6", "e5", "d5")
    assert touched(board, board.parse_san("exd6")) == {chess.E5, chess.D6, chess.D5}
