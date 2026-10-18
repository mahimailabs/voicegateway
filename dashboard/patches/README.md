# Scalar accessibility patches

Applied by postinstall with patch-package. A patch failure fails installation.

- api-client 3.22.0: expose aria-expanded=false on closed variable comboboxes. Open dropdowns retain the upstream listbox relationship.
- api-reference 1.73.1: make the scrollable endpoint list keyboard focusable.

These are production fixes, not test exclusions. Remove each patch when a pinned upstream release includes the fix and the full light/dark browser suite passes. Keep the dependency versions and patches in sync.
