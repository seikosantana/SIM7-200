"""Headless runner: `python -m sim7200 run <project.json> --scans N --dump ADDR,ADDR`."""
from __future__ import annotations

import argparse
import sys

from .memory import parse_address
from .project import build_interpreter, load_project, scan_order_sbr_nums


def run(args: argparse.Namespace) -> int:
    proj, subs_by_num, subs_by_name = load_project(args.project)
    interp = build_interpreter(proj, subs_by_num)
    order = scan_order_sbr_nums(proj, subs_by_name)
    for _ in range(args.scans):
        interp.run_scan(order)

    if args.dump:
        for token in args.dump.split(","):
            token = token.strip()
            addr = parse_address(token)
            if addr is None:
                print(f"{token}: not a valid address", file=sys.stderr)
                continue
            print(f"{token} = {interp.mem.read(addr)}")
    return 0


def gui(args: argparse.Namespace) -> int:
    from .gui import run_gui

    run_gui(port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim7200")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a project headlessly for N scans")
    run_p.add_argument("project", help="path to a project JSON file")
    run_p.add_argument("--scans", type=int, default=1)
    run_p.add_argument("--dump", default="", help="comma-separated address tokens to print after the run")
    run_p.set_defaults(func=run)

    gui_p = sub.add_parser("gui", help="launch the web GUI (pick a project from the browser)")
    gui_p.add_argument("--port", type=int, default=8080)
    gui_p.set_defaults(func=gui)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
