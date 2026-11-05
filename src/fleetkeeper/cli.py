# -*- coding: utf-8 -*-
"""fleetkeeper CLI.

    fleetkeeper status [SERVICE] [--json]     evidence ladder per service
    fleetkeeper doctor [--json]               fleet-wide diagnosis
    fleetkeeper heartbeat SERVICE             write one heartbeat
    fleetkeeper sweep CONFIG                  run one watchdog sweep (cron entry)
    fleetkeeper watch CONFIG [--interval S]   run sweeps in a loop
    fleetkeeper logs SERVICE [--tail N]       show logs, redacted by default
"""
import argparse
import json
import os
import sys
import time

from . import evidence, heartbeat, proc, redact, watchdog


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_service(cfg, token):
    svcs = (cfg.get("registry") or {}).get("services") or {}
    if token in svcs:
        return token, svcs[token]
    for k, v in svcs.items():
        if v.get("name") == token or str(v.get("port")) == str(token):
            return k, v
    return None, None


def _registry(cfg):
    out = {}
    for key, svc in ((cfg.get("registry") or {}).get("services") or {}).items():
        item = dict(svc)
        item["key"] = key
        item.setdefault("name", key)
        out[key] = item
    return out


def cmd_status(args):
    cfg = _load(args.config)
    svcs = _registry(cfg)
    if args.service:
        key, svc = _resolve_service(cfg, args.service)
        if svc is None:
            print("unknown service: {}".format(args.service))
            return 2
        svcs = {key: svc}

    rows = []
    for key, svc in svcs.items():
        ev = evidence.evaluate_service(svc)
        hb = heartbeat.evaluate(key, int(svc.get("heartbeat_period") or 60),
                                directory=_hb_dir(cfg))
        rows.append({"key": key, "name": svc["name"], "port": svc.get("port"),
                     "level": ev["level_name"], "pids": ev.get("pids", []),
                     "heartbeat": hb["status"], "hb_age": hb.get("age_seconds"),
                     "evidence": ev["evidence"], "issues": ev["issues"]})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print("{:<22} {:<7} {:<13} {:<14} {}".format("NAME", "PORT", "EVIDENCE", "HEARTBEAT", "PIDS"))
    for r in rows:
        print("{:<22} {:<7} {:<13} {:<14} {}".format(
            r["name"][:22], r["port"] or "-", r["level"], r["heartbeat"],
            ",".join(map(str, r["pids"])) or "-"))
    for r in rows:
        if r["issues"] or r["heartbeat"] not in ("UP",):
            print()
            print("[{}] :{}".format(r["name"], r["port"]))
            for i in r["issues"]:
                print("    issue: {}".format(i))
    print()
    print("evidence stops at L4 (business canary); L5 (user-visible outcome) "
          "is never auto-claimed.")
    return 0


def cmd_doctor(args):
    cfg = _load(args.config)
    svcs = _registry(cfg)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "services": [], "p0": [], "uncovered": []}
    for key, svc in svcs.items():
        ev = evidence.evaluate_service(svc)
        hb = heartbeat.evaluate(key, int(svc.get("heartbeat_period") or 60),
                                directory=_hb_dir(cfg))
        item = {"service": svc["name"], "port": svc.get("port"),
                "level": ev["level_name"], "issues": ev["issues"],
                "heartbeat": hb, "recovery": svc.get("recovery")}
        report["services"].append(item)
        if ev["level"] < 2 or hb["status"] in ("STALE", "FAILING"):
            report["p0"].append(item)
        elif hb["status"] in ("NO_HEARTBEAT", "ALIVE_ONLY", "PROTOCOL_ONLY"):
            report["uncovered"].append(item)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("fleetkeeper doctor  {}".format(report["generated_at"]))
    print("=" * 68)
    for item in report["services"]:
        if item in report["p0"]:
            flag = "P0"
        elif item in report["uncovered"]:
            flag = "??"
        else:
            flag = "OK"
        print("[{}] {:<22} :{:<6} {}".format(
            flag, item["service"], item["port"], item["level"]))
        print("      heartbeat: {}  {}".format(
            item["heartbeat"]["status"], item["heartbeat"]["detail"]))
    print("=" * 68)
    if report["p0"]:
        print("P0 failures (broken for real):")
        for item in report["p0"]:
            print("  - {} :{}".format(item["service"], item["port"]))
            for i in item["issues"]:
                print("      {}".format(i))
    else:
        print("P0 failures: none. All services reached L2 (port healthy) or better.")
    if report["uncovered"]:
        print()
        print("Insufficiently covered (not failures, but work is unproven):")
        for item in report["uncovered"]:
            print("  - {} : heartbeat={}".format(
                item["service"], item["heartbeat"]["status"]))
        print("  a live port proves the process is not dead; it does not prove work.")
    return 0


def cmd_heartbeat(args):
    cfg = _load(args.config)
    key, svc = _resolve_service(cfg, args.service)
    if svc is None:
        print("unknown service: {}".format(args.service))
        return 2
    path = heartbeat.write(key, processed=args.processed, ok=not args.fail,
                           extra={"proof": args.proof or "business"},
                           directory=_hb_dir(cfg))
    print("heartbeat written: {} (proof={})".format(path, args.proof or "business"))
    return 0


def cmd_sweep(args):
    summary = watchdog.run(args.config)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        if summary["network_down"]:
            print("network DOWN - healing suspended, issues recorded only")
        for i in summary["issues"]:
            print("issue: {}".format(i))
        for h in summary["healed"]:
            print("healed: {}".format(h))
        for a in summary["alerts"]:
            print("alert: {}".format(a))
        if not summary["issues"] and not summary["network_down"]:
            print("fleet healthy")
    return 0


def cmd_watch(args):
    try:
        while True:
            summary = watchdog.run(args.config)
            stamp = time.strftime("%H:%M:%S")
            state = "DOWN-net" if summary["network_down"] else (
                "issues={}".format(len(summary["issues"])) if summary["issues"] else "ok")
            print("[{}] sweep: {} healed={} alerts={}".format(
                stamp, state, len(summary["healed"]), len(summary["alerts"])),
                flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_logs(args):
    cfg = _load(args.config)
    key, svc = _resolve_service(cfg, args.service)
    if svc is None:
        print("unknown service: {}".format(args.service))
        return 2
    base = os.path.dirname(os.path.abspath(args.config))
    logs = svc.get("logs") or {}
    path = logs.get("stderr") or logs.get("stdout")
    if path:
        path = path if os.path.isabs(path) else os.path.normpath(os.path.join(base, path))
    if not path or not os.path.exists(path):
        print("{} declares no existing log file: {}".format(svc["name"], path))
        return 2
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()[-args.tail:]
    out = "\n".join(lines)
    if not args.raw:
        hits = redact.scan(out)
        out = redact.redact(out)
        if hits:
            print("# redacted {} secret pattern(s): {}".format(
                len(hits), ", ".join(n for n, _ in hits)))
    print(out)
    return 0


def _hb_dir(cfg):
    base = os.path.dirname(os.path.abspath(getattr(_load, "_path", "")) or ".")
    d = (cfg.get("heartbeat_dir") or "runtime/heartbeat")
    return d if os.path.isabs(d) else os.path.join(base or ".", d)


def build_parser():
    p = argparse.ArgumentParser(
        prog="fleetkeeper",
        description="Evidence-based guardian for a fleet of local AI services.")
    p.add_argument("--config", default="config/fleet.json",
                   help="fleet config JSON (default: config/fleet.json)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="evidence ladder per service")
    s.add_argument("service", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("doctor", help="fleet-wide diagnosis")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    h = sub.add_parser("heartbeat", help="write one heartbeat")
    h.add_argument("service")
    h.add_argument("--processed", type=int, default=None)
    h.add_argument("--fail", action="store_true")
    h.add_argument("--proof", default=None,
                   choices=["business", "process_alive", "protocol_only"])
    h.set_defaults(func=cmd_heartbeat)

    w = sub.add_parser("sweep", help="run one watchdog sweep (cron entry)")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_sweep)

    w2 = sub.add_parser("watch", help="run sweeps in a loop")
    w2.add_argument("--interval", type=int, default=60)
    w2.set_defaults(func=cmd_watch)

    lg = sub.add_parser("logs", help="show service logs (redacted by default)")
    lg.add_argument("service")
    lg.add_argument("--tail", type=int, default=40)
    lg.add_argument("--raw", action="store_true")
    lg.set_defaults(func=cmd_logs)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    # remember the config path for relative resolution inside helpers
    _load._path = os.path.abspath(args.config)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print("config not found: {} ({})".format(args.config, e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
