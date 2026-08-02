#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging

from turnstile_solver.api import create_app
from turnstile_solver.config import SolverConfig


def parse_args() -> argparse.Namespace:
    env = SolverConfig.from_env()
    parser = argparse.ArgumentParser(description="GrokRegisterAgent Turnstile solver service")
    parser.add_argument("--host", default=env.host)
    parser.add_argument("--port", type=int, default=env.port)
    parser.add_argument("--thread", type=int, default=env.workers)
    parser.add_argument("--browser-type", "--browser_type", dest="backend", default=env.backend)
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--debug", action="store_true", default=env.debug)
    parser.add_argument("--proxy", action="store_true", default=env.proxy_support)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = SolverConfig.from_env()
    config = env.with_cli(
        host=args.host,
        port=args.port,
        workers=max(1, min(16, args.thread)),
        backend=args.backend.strip().lower(),
        headless=not args.no_headless,
        debug=args.debug,
        proxy_support=args.proxy,
    )
    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(config)
    app.run(host=config.host, port=config.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
