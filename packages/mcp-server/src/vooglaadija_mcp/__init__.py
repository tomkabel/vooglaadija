"""Official Model Context Protocol (MCP) server for Vooglaadija."""

from .cli import main
from .client import VooglaadijaApiError, VooglaadijaClient
from .server import VooglaadijaMCPServer

__version__ = "1.0.0"

__all__ = [
    "VooglaadijaApiError",
    "VooglaadijaClient",
    "VooglaadijaMCPServer",
    "main",
]
