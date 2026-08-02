"""Module entry point for ``python -m harbor``."""

from harbor.cli import main


if __name__ == "__main__":
    raise SystemExit(main())