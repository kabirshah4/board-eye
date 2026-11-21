"""Reads moves off a real board with plain image processing: free, offline, ~5 ms.

The four board corners, tapped once on the phone, let each still photo be
flattened into a top-down 8x8 grid. Each square is compared with the last
photo the game accepted; the legal move whose squares changed (2 for a move,
3 for en passant, 4 for castling) and that leaves nothing else unexplained wins.
A white piece on a cream square barely changes the colour there, so every one
of a move's squares must change a little, but they are ranked by their average.
"""

import time

import chess
import cv2
import numpy as np

from .vision import Read

SIZE = 400  # the flattened board, in pixels
SQ = SIZE // 8
INNER = slice(int(SQ * 0.2), int(SQ * 0.8))  # the middle of a square, away from its neighbours' pieces

# Where each tapped corner lands in the flattened board, which always has a8 top-left.
# Taps are made from the player's seat: near-left, near-right, far-right, far-left.
TARGETS = {
    chess.WHITE: [(0, SIZE), (SIZE, SIZE), (SIZE, 0), (0, 0)],  # a1, h1, h8, a8
    chess.BLACK: [(SIZE, 0), (0, 0), (0, SIZE), (SIZE, SIZE)],  # h8, a8, a1, h1
}


def touched(board: chess.Board, move: chess.Move) -> frozenset[int]:
    """Every square whose contents `move` changes."""
    squares = {move.from_square, move.to_square}
    if board.is_castling(move):
        rank = chess.square_rank(move.from_square)
        kingside = chess.square_file(move.to_square) > chess.square_file(move.from_square)
        squares |= {chess.square(7, rank), chess.square(5, rank)} if kingside else {chess.square(0, rank), chess.square(3, rank)}
    elif board.is_en_passant(move):
        squares.add(chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square)))
    return frozenset(squares)


class LocalReader:
    reads_own_moves = True
    needs_corners = True

    def __init__(self, min_confidence: float = 0.3, min_change: float = 8.0) -> None:
        self.min_confidence = min_confidence
        self.min_change = min_change  # mean Lab distance a move's squares must change by, on average
        self.corners: list[list[float]] | None = None
        self.my_color = chess.WHITE
        self.reference: np.ndarray | None = None

    @property
    def ready(self) -> bool:
        return self.corners is not None and self.reference is not None

    def set_corners(self, points: list[list[float]], my_color: chess.Color) -> None:
        """`points`: the four taps as 0-1 fractions of the frame, near-left first, clockwise."""
        if len(points) != 4:
            raise ValueError("need 4 corners")
        self.corners, self.my_color = points, my_color
        self.reference = None

    def flatten(self, jpeg: bytes) -> np.ndarray:
        if self.corners is None:
            raise ValueError("board corners not set")
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("not a JPEG")
        height, width = image.shape[:2]
        src = np.float32([[x * width, y * height] for x, y in self.corners])
        transform = cv2.getPerspectiveTransform(src, np.float32(TARGETS[self.my_color]))
        top = cv2.warpPerspective(image, transform, (SIZE, SIZE))
        top = cv2.GaussianBlur(top, (5, 5), 0)
        return cv2.cvtColor(top, cv2.COLOR_BGR2LAB).astype(np.float32)

    def commit(self, jpeg: bytes) -> None:
        """Make this photo the one the next move is measured against."""
        self.reference = self.flatten(jpeg)

    def changes(self, flat: np.ndarray) -> np.ndarray:
        """How much each square (indexed like python-chess) changed since the reference."""
        assert self.reference is not None
        distance = np.linalg.norm(flat - self.reference, axis=2)
        out = np.empty(64, np.float32)
        for square in chess.SQUARES:
            x, y = chess.square_file(square) * SQ, (7 - chess.square_rank(square)) * SQ
            out[square] = distance[y : y + SQ, x : x + SQ][INNER, INNER].mean()
        # A cloud or a shadow moves every square a little; only what stands out is a move.
        return np.clip(out - np.median(out), 0, None)

    def score(self, board: chess.Board, change: np.ndarray) -> list[tuple[float, chess.Move]]:
        """Each distinct set of touched squares, best explanation first (queen for promotions)."""
        best: dict[frozenset[int], tuple[float, chess.Move]] = {}
        for move in board.legal_moves:
            squares = touched(board, move)
            if squares in best and move.promotion != chess.QUEEN:
                continue
            inside = [change[s] for s in squares]
            if min(inside) < self.min_change / 2:
                continue  # one of its squares did not change: not this move
            outside = max((change[s] for s in chess.SQUARES if s not in squares), default=0.0)
            best[squares] = (float(np.mean(inside) - 0.5 * outside), move)
        return sorted(best.values(), key=lambda pair: pair[0], reverse=True)

    async def which_move(self, jpeg: bytes, board: chess.Board) -> Read:
        started = time.perf_counter()
        flat = self.flatten(jpeg)
        change = self.changes(flat)
        ranked = self.score(board, change)
        ms = (time.perf_counter() - started) * 1000
        top, move = ranked[0] if ranked else (0.0, None)
        if move is None or top < self.min_change:
            if change.max() < self.min_change / 2:
                self.reference = flat  # nothing moved: keep up with slow light changes
            return Read(None, 1.0, ms)
        second = max(ranked[1][0], 0.0) if len(ranked) > 1 else 0.0
        return Read(move, 1 - second / top, ms)

