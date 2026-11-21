"""Fake phone photos of a board: drawn top-down, then tilted into a camera view."""

import chess
import cv2
import numpy as np

# Where the board's corners land in a 1280x960 photo taken from White's side:
# near-left (a1), near-right (h1), far-right (h8), far-left (a8).
WHITE_SEAT = [(0.12, 0.92), (0.88, 0.92), (0.74, 0.30), (0.26, 0.30)]
W, H = 1280, 960
LIGHT, DARK = (225, 235, 238), (60, 110, 50)  # BGR: cream and green, like a park board


def topdown(board: chess.Board, size: int = 800) -> np.ndarray:
    sq = size // 8
    img = np.zeros((size, size, 3), np.uint8)
    for square in chess.SQUARES:
        f, r = chess.square_file(square), chess.square_rank(square)
        x, y = f * sq, (7 - r) * sq
        img[y : y + sq, x : x + sq] = LIGHT if (f + r) % 2 else DARK
        piece = board.piece_at(square)
        if piece:
            color = (245, 245, 245) if piece.color else (25, 25, 25)
            radius = int(sq * (0.22 + 0.03 * piece.piece_type))
            cv2.circle(img, (x + sq // 2, y + sq // 2), radius, color, -1)
            cv2.circle(img, (x + sq // 2, y + sq // 2), radius, (120, 120, 120), 2)
    return img


def photo(board: chess.Board, seat: chess.Color = chess.WHITE, light: float = 1.0, seed: int = 0) -> bytes:
    """A JPEG as the phone would send it, seen from `seat`'s side of the table."""
    top = topdown(board)
    if seat == chess.BLACK:
        top = cv2.rotate(top, cv2.ROTATE_180)
    s = top.shape[0]
    src = np.float32([(0, s), (s, s), (s, 0), (0, 0)])
    dst = np.float32([(x * W, y * H) for x, y in WHITE_SEAT])
    img = cv2.warpPerspective(top, cv2.getPerspectiveTransform(src, dst), (W, H), borderValue=(90, 120, 140))
    rng = np.random.default_rng(seed)
    img = np.clip(img.astype(np.float32) * light + rng.normal(0, 6, img.shape), 0, 255).astype(np.uint8)
    img = cv2.resize(img, (768, 576))
    ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    assert ok
    return jpeg.tobytes()
