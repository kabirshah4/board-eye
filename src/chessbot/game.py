"""The game: the one source of truth for the position and whose turn it is."""

from dataclasses import dataclass

import chess

PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


@dataclass(frozen=True)
class MoveLabel:
    uci: str
    text: str  # on screen: "KNIGHT d4 → f3 · CHECKMATE"
    speech: str  # in the earbud: "Knight f3, checkmate"
    log: str  # in the log: "knight d4->f3 (checkmate)"
    is_mate: bool


def describe(board: chess.Board, move: chess.Move) -> MoveLabel:
    """Name `move`, played from `board`, for the screen, the earbud and the log."""
    piece = board.piece_at(move.from_square)
    if piece is None:
        raise ValueError(f"no piece on {chess.square_name(move.from_square)}")
    name = PIECE_NAMES[piece.piece_type]
    frm, to = chess.square_name(move.from_square), chess.square_name(move.to_square)

    after = board.copy(stack=False)
    after.push(move)
    is_mate = after.is_checkmate()
    suffix = "checkmate" if is_mate else "check" if after.is_check() else None

    if board.is_castling(move):
        side = "kingside" if chess.square_file(move.to_square) > chess.square_file(move.from_square) else "queenside"
        text, speech, log = f"CASTLE {side.upper()}", f"Castle {side}", f"castle {side}"
        notes = []
    else:
        captured = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
        takes = " takes" if captured else ""
        notes = [f"takes {PIECE_NAMES[captured]}"] if captured else []
        promo = f" {PIECE_NAMES[move.promotion]}" if move.promotion else ""
        if move.promotion:
            notes.append(f"promotes to {PIECE_NAMES[move.promotion]}")
        text = f"{name.upper()} {frm} → {to}{promo.upper()}"
        speech = f"{name.capitalize()}{takes} {to}{promo}"
        log = f"{name} {frm}->{to}"

    if suffix:
        text += f" · {suffix.upper()}"
        speech += f", {suffix}"
        notes.append(suffix)
    if notes:
        log += f" ({', '.join(notes)})"
    return MoveLabel(move.uci(), text, speech, log, is_mate)


class Game:
    def __init__(self, my_color: chess.Color = chess.WHITE) -> None:
        self.board = chess.Board()
        self.my_color = my_color
        self.suggested: chess.Move | None = None

    @property
    def is_my_turn(self) -> bool:
        return self.board.turn == self.my_color

    @property
    def is_over(self) -> bool:
        return self.board.is_game_over()

    def legal(self) -> list[chess.Move]:
        return list(self.board.legal_moves)

    def push(self, move: chess.Move) -> MoveLabel:
        if move not in self.board.legal_moves:
            raise ValueError(f"illegal move {move.uci()}")
        label = describe(self.board, move)
        self.board.push(move)
        self.suggested = None
        return label

    def undo(self) -> chess.Move | None:
        self.suggested = None
        return self.board.pop() if self.board.move_stack else None

    def parse(self, text: str) -> chess.Move:
        """A move typed by hand, as SAN (`Nf3`, `e5`) or UCI (`g1f3`)."""
        text = text.strip()
        try:
            return self.board.parse_san(text)
        except ValueError:
            move = chess.Move.from_uci(text.lower())
            if move not in self.board.legal_moves:
                raise ValueError(f"illegal move {text}") from None
            return move

    def snapshot(self) -> dict:
        return {
            "fen": self.board.fen(),
            "my_color": "white" if self.my_color == chess.WHITE else "black",
            "my_turn": self.is_my_turn,
            "moves": len(self.board.move_stack),
            "over": self.is_over,
            "suggested": self.suggested.uci() if self.suggested else None,
        }
