"""Find out whether Jev can see images, which `state` shape it takes, and how fast it is.

    uv run scripts/probe_jev.py path/to/board-photo.jpg

For each image format it asks one yes/no question about the board photo and
the same question about a plain grey image. A format Jev really reads answers
yes for the board and no for the grey one; a format it ignores answers alike.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from chessbot.vision import Jev, JevError, build_state  # noqa: E402

FORMATS = ("blocks", "image_url", "object")
QUESTION = {
    "board": {
        "type": "noul",
        "instructions": "Does the photo show a chessboard with chess pieces on it?",
        "criteria": {"true": "A chessboard with pieces is visible", "false": "No chessboard is visible"},
    }
}


def grey_jpeg() -> bytes:
    path = Path(tempfile.mkdtemp()) / "grey.jpg"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=768x576", "-frames:v", "1", str(path)],
        check=True,
    )
    return path.read_bytes()


async def main() -> None:
    load_dotenv()
    key = os.environ.get("JEV_API_KEY")
    if not key:
        sys.exit("Put JEV_API_KEY=... in .env first.")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    board, grey = Path(sys.argv[1]).read_bytes(), grey_jpeg()

    jev = Jev(key, timeout=15)
    try:
        # Warm the connection so the timings below are per call, not per handshake.
        try:
            await jev.evaluate("warm up", {"x": {"type": "noul", "instructions": "Is this text?"}})
        except JevError as e:
            sys.exit(f"Jev rejected a plain text call, check the key: {e}")

        working = []
        for fmt in FORMATS:
            row = [f"{fmt:10}"]
            scores = []
            for name, jpeg in (("board", board), ("grey", grey)):
                started = time.perf_counter()
                try:
                    answers = await jev.evaluate(build_state(fmt, "A photo.", jpeg), QUESTION)
                    score = float(answers["board"]["noul"])
                    scores.append(score)
                    row.append(f"{name} {score:.2f} ({(time.perf_counter() - started) * 1000:.0f}ms)")
                except (JevError, KeyError) as e:
                    row.append(f"{name} ERROR {e}")
            print("  ".join(row))
            if len(scores) == 2 and scores[0] > 0.7 and scores[1] < 0.3:
                working.append(fmt)
    finally:
        await jev.close()

    if working:
        print(f"\nJev sees images. Put JEV_IMAGE_FORMAT={working[0]} in .env")
    else:
        print("\nNo format made Jev tell the board from grey: it isn't reading the image.")


if __name__ == "__main__":
    asyncio.run(main())
