"""FastAPI fixture that actually listens on port 8000, despite what the README claims."""
from fastapi import FastAPI

PORT = 8000

app = FastAPI()


@app.get("/")
def root() -> dict:
    return {"ok": True, "port": PORT}
