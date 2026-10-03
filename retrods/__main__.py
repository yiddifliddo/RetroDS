"""Entry point: ``python -m retrods`` opens the GUI, any sub-command runs the CLI."""

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])
    from .gui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())
