"""Vercel entrypoint: Vercel looks for a FastAPI `app` in a root app.py. Locally, run `python -m cfl serve`."""

from cfl.api import app  # noqa: F401
