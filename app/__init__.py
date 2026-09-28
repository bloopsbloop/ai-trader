"""Importing research and paper modules must not initialize an exchange client."""

__all__ = ["Application"]


def __getattr__(name):
    if name == "Application":
        from .application import Application
        return Application
    raise AttributeError(name)



