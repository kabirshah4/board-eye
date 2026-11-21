# Board Eye

Your phone watches a real chessboard. When a move settles, the laptop works out which legal move
was played, Stockfish answers, and the phone speaks the reply into your earbuds.

```
iPhone home-screen app ──still photo──▶ Mac: which legal move? → Stockfish (reply already worked out)
        ▲                                                  │
        └────────── "Knight f3" (speech + screen) ◀────────┘        via Tailscale HTTPS
```

Built for speed: about 10–30 ms on the laptop from the photo arriving to the reply going back.
- The phone detects motion itself and sends one photo when the board goes still, not a stream.
- The board is read with plain image processing on the laptop: you tap the board's four corners
  once, each photo is flattened to an 8×8 grid, and the legal move whose squares changed wins.
  It takes about 5 ms, costs nothing, and works offline.
- While the opponent thinks, Stockfish works out a reply to each of their legal moves, so the
  answer is ready the moment their move is read.
- Your own moves are read too, so playing something other than the suggestion is fine.
- Jev (TypeSafe) can read the opponent's moves instead, with `BOARD_READER=jev` in `.env`. That
  needs paid Vercel AI Gateway credits.

## Setup (once)

1. `brew install stockfish`
2. Install Tailscale on this Mac (`brew install --cask tailscale-app`) and on your iPhone
   (App Store), signed into the same account. In the Tailscale admin console, turn on
   **MagicDNS** and **HTTPS certificates**.

## Every time

```
./run.sh
```

It prints `https://<your-mac>.<tailnet>.ts.net`. Open that once in Safari on the iPhone, tap
Share, then **Add to Home Screen**.

In a game:
1. Open the app, pick White or Black, tap **Start**.
2. Prop the phone on your side of the table so the whole board is in view.
3. Tap the board's four corners as it asks (near-left, near-right, far-right, far-left from your
   seat). The cyan outline should sit on the board's edge.
4. Put your earbuds in and play.

If the phone gets bumped, tap **Corners** and tap them again. If a move is misread, use **Undo**,
or type the actual move (`e5`, `Nf3`) and tap **Set**. **Read now** sends a photo immediately.

## Tests

`uv run pytest`. The board reader is tested on rendered board photos, and Stockfish runs for real.

Casual games only. Keep this out of rated, tournament or money games.
