"""Run data transfer from any directory using the installed project environment."""
from pathlib import Path
import sys

from hermes_zalo.state_transfer import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "export" and "--project-root" not in args:
        args.extend(["--project-root", str(Path(__file__).resolve().parent.parent)])
    raise SystemExit(main(args))
