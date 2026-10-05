"""Render After Effects MOGRT templates without Adobe software."""

__version__ = "0.6.5"

from .mogrt import Mogrt, Control, load

__all__ = ["Mogrt", "Control", "load"]
