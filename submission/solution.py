"""Entry point loaded by the harness: build() returns the knowledge system.

The code lives in the `truenow` package next to this file. Keeping this module
thin matters: the harness loads it without registering it in sys.modules.
"""
from truenow.system import build

__all__ = ["build"]
