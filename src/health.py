import asyncio

from aiohttp import web

from src.config import VERSION
from src.security.admin_allowlist import is_admin
from src.usage.logger import read_recent


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "version": VERSION})


async def _admin_usage(request: web.Request) -> web.Response:
    user_id = request.headers.get("X-Slack-User-ID", "")
    if not is_admin(user_id):
        return web.json_response({"error": "forbidden"}, status=403)
    entries = read_recent(limit=50)
    return web.json_response({"entries": entries})


def create_health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/admin/usage", _admin_usage)
    return app


def run_health_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    # web.run_app() installs signal handlers, which only works on the main
    # thread. We start the bot on the main thread and the health server on a
    # worker thread, so use AppRunner/TCPSite directly with a fresh loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(create_health_app())
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, host=host, port=port)
    loop.run_until_complete(site.start())
    loop.run_forever()
