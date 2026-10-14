"""Point d'entrée conservé pour `uvicorn main:app` (Dockerfile, Railway, Render)."""

from app.main import app  # noqa: F401
