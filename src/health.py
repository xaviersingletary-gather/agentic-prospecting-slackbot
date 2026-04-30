from aiohttp import web

from src.config import VERSION


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "version": VERSION})


def create_health_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", _health)
    return app


def run_health_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    web.run_app(create_health_app(), host=host, port=port, print=None)
