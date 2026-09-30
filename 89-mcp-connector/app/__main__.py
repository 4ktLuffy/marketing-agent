"""python -m app stdio   (Claude Desktop, Claude Code: the client starts this process)
python -m app http    (streamable HTTP on MCP_HOST:MCP_PORT/mcp, for a remote connector)"""
import logging
import sys

from . import config


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    transport = argv[0] if argv else "stdio"
    if transport not in ("stdio", "http"):
        print("usage: python -m app [stdio|http]", file=sys.stderr)
        return 2
    # stdout carries the protocol on stdio: every log line goes to stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("mcp").setLevel(logging.WARNING)  # the SDK's INFO lines may quote tool errors
    try:
        cfg = config.load(transport=transport)
    except config.ConfigError as exc:
        print(f"mcp-connector: {exc}", file=sys.stderr)
        return 1
    if transport == "stdio":
        from .server import build_server
        build_server(cfg).run("stdio")
        return 0
    import uvicorn

    from .web import build_app
    uvicorn.run(build_app(cfg), host=cfg.host, port=cfg.port, access_log=False, log_level="warning",
                proxy_headers=False, server_header=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
