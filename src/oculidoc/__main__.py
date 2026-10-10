"""Command dispatcher for the OculiDoC desktop application."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def _restore_frozen_stdio() -> None:
    """Reopen redirected Win32 handles hidden by PyInstaller's GUI bootloader."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    import ctypes
    import msvcrt
    import os
    from ctypes import wintypes

    get_std_handle = ctypes.WinDLL("kernel32", use_last_error=True).GetStdHandle
    get_std_handle.argtypes = [wintypes.DWORD]
    get_std_handle.restype = wintypes.HANDLE
    for name, handle_id, flags, mode in (
        ("stdin", -10, os.O_RDONLY, "r"),
        ("stdout", -11, os.O_WRONLY, "w"),
        ("stderr", -12, os.O_WRONLY, "w"),
    ):
        if getattr(sys, name) is not None:
            continue
        handle = get_std_handle(handle_id)
        if handle in (None, 0, ctypes.c_void_p(-1).value):
            if name == "stderr":
                sys.stderr = open(os.devnull, "w", encoding="utf-8")
                continue
            raise RuntimeError(f"Frozen process has no redirected {name} handle.")
        fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
        setattr(sys, name, os.fdopen(fd, mode, encoding="utf-8", errors="replace", buffering=1))


def dispatch(
    argv: Sequence[str] | None = None,
) -> int:
    """Route desktop, local API, package-smoke, and child-task invocations."""

    arguments = list(sys.argv[1:] if argv is None else argv)

    if arguments == ["--thinks-worker"]:
        _restore_frozen_stdio()
        from oculidoc.vendor_helpers.thinks_tracker_worker import main as run_thinks_worker

        return run_thinks_worker()

    if arguments == ["--stdio-smoke"]:
        _restore_frozen_stdio()
        import json

        from oculidoc.updater import installed_version

        if sys.stdin.readline().strip() != "OCULIDOC_STDIO_PROBE":
            return 2
        print(json.dumps({"status": "ok", "version": installed_version()}), flush=True)
        return 0

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
        _restore_frozen_stdio()
        if len(arguments) != 3 or arguments[1] != "--repo":
            raise SystemExit("--update requires --repo and one repository path.")

        import json

        from oculidoc.updater import perform_update

        try:
            result = perform_update(Path(arguments[2]))
        except Exception as error:  # noqa: BLE001 -- command boundary returns structured failure.
            print(json.dumps({"status": "error", "message": str(error)}))
            return 1

        print(json.dumps(result))
        return 0

    if arguments == ["--install-latest-release"]:
        _restore_frozen_stdio()
        import json

        from oculidoc.updater import install_latest_release

        try:
            result = install_latest_release()
        except Exception as error:  # noqa: BLE001 -- command boundary returns structured failure.
            print(json.dumps({"status": "error", "message": str(error)}))
            return 1

        print(json.dumps(result))
        return 0

    if arguments[:1] == ["--task"]:
        if len(arguments) < 2:
            raise SystemExit("--task requires a task command.")

        from oculidoc.tasks.__main__ import (
            main as run_task,
        )

        return run_task(arguments[1:])

    if arguments[:1] == ["--signal-task"]:
        from oculidoc.signal_tasks.__main__ import main as run_signal_task

        return run_signal_task(arguments[1:])

    if arguments:
        raise SystemExit("Unsupported OculiDoC arguments: " + " ".join(arguments))

    from oculidoc.app import run

    return run()


def main(
    argv: Sequence[str] | None = None,
) -> None:
    """Run the requested OculiDoC process mode."""

    raise SystemExit(dispatch(argv))


if __name__ == "__main__":
    main()
