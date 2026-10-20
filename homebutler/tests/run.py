"""Run the rebuilt service's tests; on GitHub Actions also report each failure as an annotation, so
the reason is visible without opening the full log. Usage: python -m homebutler.tests.run"""
import os
import sys
import unittest


def main() -> int:
    suite = unittest.defaultTestLoader.discover("homebutler/tests", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("GITHUB_ACTIONS"):
        for test, trace in result.failures + result.errors:
            lines = trace.strip().splitlines()
            message = "%0A".join(lines[-12:]).replace("::", ": :")
            print(f"::error title={test.id()}::{message}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
