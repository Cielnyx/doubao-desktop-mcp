"""Validate the base interpreter before installing a local environment."""
import json
import sys

if sys.platform != "win32" or sys.version_info < (3, 11):
    raise SystemExit("Windows and Python 3.11+ are required (Python 3.12 recommended).")
print(json.dumps({"base_prefix": sys.base_prefix, "version": list(sys.version_info[:2])}))
