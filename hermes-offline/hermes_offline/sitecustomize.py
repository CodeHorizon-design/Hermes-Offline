"""
sitecustomize.py — auto-apply hermes-offline patches on startup.

When installed to the Python site-packages alongside hermes-agent,
this file is executed automatically by Python before any user code,
ensuring offline patches are applied before hermes_cli imports.

Install (one-time):
    python -c "import site; print(site.getsitepackages()[0])"
    # Copy this file to the printed directory/sitecustomize.py
    # OR use the entry point: hermes-offline-patch

This is optional — you can also call hermes_offline.apply() explicitly
at the top of any entry point that uses hermes.
"""

import os

# Only auto-patch if HERMES_OFFLINE=1 or HERMES_NO_CLOUD=1
_should_patch = (
    os.environ.get("HERMES_OFFLINE") == "1"
    or os.environ.get("HERMES_NO_CLOUD") == "1"
    or os.path.exists(os.path.expanduser("~/.hermes/.offline"))
)

if _should_patch:
    try:
        from hermes_offline import apply
        apply()
    except ImportError:
        pass
