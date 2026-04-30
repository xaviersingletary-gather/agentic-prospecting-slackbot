import os
import runpy
import threading

from src.env_validator import validate_required_env_vars


def _start_health_server() -> None:
    from src.health import run_health_server

    port = int(os.getenv("PORT", "8080"))
    run_health_server(host="0.0.0.0", port=port)


def main() -> None:
    validate_required_env_vars()
    threading.Thread(target=_start_health_server, daemon=True).start()
    runpy.run_module("src.main", run_name="__main__")


if __name__ == "__main__":
    main()
