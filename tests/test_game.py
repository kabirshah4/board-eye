import chess
import pytest

from chessbot.game import Game, describe


def label(fen: str, uci: str):
    return describe(chess.Board(fen), chess.Move.from_uci(uci))


def test_plain_move():
    l = label(chess.STARTING_FEN, "g1f3")
    assert (l.text, l.speech, l.log) == ("KNIGHT g1 → f3", "Knight f3", "knight g1->f3")


def test_capture_and_check():
    # 1.e4 e5 2.Nf3 d6 3.Bc4 Bg4 4.Nc3 g6 5.Nxe5: knight takes pawn
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "d6", "Bc4", "Bg4", "Nc3", "g6"]:
        board.push_san(san)
    l = describe(board, board.parse_san("Nxe5"))
    assert l.speech == "Knight takes e5"
    assert l.log == "knight f3->e5 (takes pawn)"
    board.push_san("Nxe5")
    board.push_san("Bxd1")
    l = describe(board, board.parse_san("Bxf7+"))
    assert l.log == "bishop c4->f7 (takes pawn, check)"
    assert l.speech == "Bishop takes f7, check"


def test_checkmate():
    # Fool's mate: 1.f3 e5 2.g4 Qh4#
    board = chess.Board()
    for san in ["f3", "e5", "g4"]:
        board.push_san(san)
    l = describe(board, board.parse_san("Qh4#"))
    assert l.is_mate
    assert l.text == "QUEEN d8 → h4 · CHECKMATE"
    assert l.speech == "Queen h4, checkmate"


def test_castling_and_promotion():
    l = label("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1c1")
    assert l.speech == "Castle queenside"
    l = label("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8q")
    assert l.speech == "Pawn e8 queen"
    assert "promotes to queen" in l.log


def test_en_passant_is_a_capture():
    l = label("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3", "e5f6")
    assert l.log == "pawn e5->f6 (takes pawn)"


def test_game_turns_parse_and_undo():
    game = Game(chess.BLACK)
    assert not game.is_my_turn
    game.push(game.parse("e4"))
    assert game.is_my_turn
    game.suggested = game.parse("e7e5")
    game.push(game.suggested)
    assert game.suggested is None
    assert game.undo() == chess.Move.from_uci("e7e5")
    with pytest.raises(ValueError):
        game.parse("Ke3")
    with pytest.raises(ValueError):
        game.push(chess.Move.from_uci("e2e4"))
