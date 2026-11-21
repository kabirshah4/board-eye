def main() -> None:
    import uvicorn

    uvicorn.run("chessbot.server:app", host="127.0.0.1", port=8000)
