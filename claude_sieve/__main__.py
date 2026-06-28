"""Allows ``python -m claude_sieve`` to work as the CLI entry point."""

from .main import main

if __name__ == '__main__':
    raise SystemExit(main())
