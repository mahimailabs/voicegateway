"""Package-data namespace for VoiceGateway.

Hosts files shipped with the wheel and read via ``importlib.resources``
from inside the package:

- ``voicegw.example.yaml`` — the rich starter config that ``voicegw init``
  writes for new users.

The file lives here (rather than at the repo root) so the wheel always
carries it and so the repo root stays uncluttered.
"""

__all__: list[str] = []
