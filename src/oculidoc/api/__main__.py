"""Local API command-line entry point."""

import uvicorn

from oculidoc.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "oculidoc.api.app:app",
        host=settings.admin_host,
        port=settings.admin_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
