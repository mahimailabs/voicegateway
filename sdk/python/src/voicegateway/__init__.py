"""VoiceGateway for LiveKit Agents: ``attach(session)``.

Importing this package imports no LiveKit module; ``attach`` loads them when called.
"""

from ._attach import Attachment, attach
from ._version import __version__

__all__ = ["Attachment", "__version__", "attach"]
