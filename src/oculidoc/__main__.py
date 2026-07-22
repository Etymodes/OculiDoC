"""Command dispatcher for the OculiDoC desktop application."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from oculidoc.app import run


def dispatch(
    argv: Sequence[str] | None = None,
) -> int:
    """Route desktop, local API, package-smoke, and child-task invocations."""

    arguments = list(sys.argv[1:] if argv is None else argv)

    if arguments[:1] == ["--package-smoke"]:
        if len(arguments) != 2:
            raise SystemExit("--package-smoke requires one output path.")

        from oculidoc.package_smoke import (
            write_package_smoke_report,
        )

        return write_package_smoke_report(Path(arguments[1]))

    if arguments == ["--api"]:
        from oculidoc.api.__main__ import (
            main as run_api,
        )

        run_api()
        return 0

    if arguments[:1] == ["--update"]:
        if len(arguments) != 3 or arguments[1] != "--repo":
            raise SystemExit("--update requires --repo and one repository path.")

        import json

        from oculidoc.updater import perform_update

        try:
            result = perform_update(Path(arguments[2]))
        except Exception as error:  # noqa: BLE001 -- command boundary returns structured failure.
            print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
            return 1

        print(json.dumps(result, ensure_ascii=False))
        return 0

    if arguments[:1] == ["--task"]:
        if len(arguments) < 2:
            raise SystemExit("--task requires a task command.")

        from oculidoc.tasks.__main__ import (
            main as run_task,
        )

        return run_task(arguments[1:])

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
