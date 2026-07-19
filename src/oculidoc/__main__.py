"""Command dispatcher for the OculiDoC desktop application."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from oculidoc.app import run


def dispatch(
    argv: Sequence[str] | None = None,
) -> int:
    """Route desktop, package-smoke, and child-task invocations."""

    arguments = list(sys.argv[1:] if argv is None else argv)

    if arguments[:1] == ["--package-smoke"]:
        if len(arguments) != 2:
            raise SystemExit("--package-smoke requires one output path.")

        from oculidoc.package_smoke import (
            write_package_smoke_report,
        )

        return write_package_smoke_report(Path(arguments[1]))

    if arguments[:1] == ["--task"]:
        if len(arguments) != 2:
            raise SystemExit("--task requires exactly one task command.")

        from oculidoc.tasks.__main__ import (
            main as run_task,
        )

        return run_task(
            [
                arguments[1],
            ]
        )

    if arguments:
        raise SystemExit("Unsupported OculiDoC arguments: " + " ".join(arguments))

    return run()


def main(
    argv: Sequence[str] | None = None,
) -> None:
    """Run the requested OculiDoC process mode."""

    raise SystemExit(dispatch(argv))


if __name__ == "__main__":
    main()
