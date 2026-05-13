"""Helpers for ``voicegateway.cli`` command modules.

One submodule per command, plus ``_shared`` for helpers used by two
or more commands. Each ``voicegateway.cli.<name>`` file imports its
helpers from the matching ``voicegateway.utils.cli.<name>`` so the
command files contain only the Typer command(s) and their bodies.
"""

__all__: list[str] = []
