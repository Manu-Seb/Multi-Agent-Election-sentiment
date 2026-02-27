try:
    from .api import app
except ModuleNotFoundError:
    app = None

__all__ = ["app"]
