"""TCSS style sheets for the v0.1.1 TUI.

Files in this package are loaded by :class:`TUIApp` via the
``CSS_PATH`` class attribute. ``main.tcss`` carries the brand
foundation (focus rings, accent colour, active-tab indicator);
per-widget layout stays in each widget's ``DEFAULT_CSS``.

This package re-exports nothing at the top level (the contents are
TCSS files, not Python). ``__all__`` is empty by design
(REQ-VG-POLISH-006 AC-1).
"""

__all__: list[str] = []
