# -*- coding: utf-8 -*-
"""fleetkeeper CLI.

    fleetkeeper                                         open the browser console
    fleetkeeper init [--config FILE] [--preset NAME]      write a runnable config
    fleetkeeper init --list-presets                       show starting scenarios
    fleetkeeper start [--port N] [--no-browser]          browser console (no typing)
    fleetkeeper status [SERVICE] [--json | --html FILE]  evidence ladder
    fleetkeeper doctor [--json | --html FILE]            fleet-wide diagnosis
    fleetkeeper heartbeat SERVICE                        write one heartbeat
    fleetkeeper sweep                                    one watchdog sweep (cron)
    fleetkeeper watch [--interval S]                     run sweeps in a loop
    fleetkeeper history [--last 24h] [--json]            incident timeline
    fleetkeeper logs SERVICE [--tail N]                  logs, redacted by default

    --config is accepted before or after the subcommand.
"""
import argparse
import json
import os
import sys
import time

from . import evidence, heartbeat, history, proc, redact, safeio, template, watchdog
from . import report as report_mod  # cmd_doctor has a local variable named `report`


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _emit_html(text, dest):
    """'-' means stdout (for piping); a real path is written atomically so a
    concurrently-opened status page never sees a half-written document."""
    if not dest:
        return
    if dest == "-":
        sys.stdout.write(text)
        return
    safeio.atomic_write_text(dest, text)
    print("wrote {}".format(os.path.abspath(dest)), file=sys.stderr)


def _resolve_service(cfg, token):
    svcs = (cfg.get("registry") or {}).get("services") or {}
    if token in svcs:
        return token, svcs[token]
    for k, v in svcs.items():
        if v.get("name") == token or str(v.get("port")) == str(token):
            return k, v
    return None, None


def _registry(cfg):
    """Merge registry.defaults into every service (service keys win).

    Without this merge a config that declares e.g. a global heartbeat_period
    looks authoritative but is silently ignored -- the worst kind of bug:
    the config lies and nothing complains.
    """
    reg = cfg.get("registry") or {}
    defaults = dict(reg.get("defaults") or {})
    out = {}
    for key, svc in (reg.get("services") or {}).items():
        item = dict(defaults)
        item.update(svc)
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
                                directory=_hb_dir(cfg, args.config_path))
        rows.append({"key": key, "name": svc["name"], "port": svc.get("port"),
                     "level": ev["level_name"], "pids": ev.get("pids", []),
                     "heartbeat": hb["status"], "hb_age": hb.get("age_seconds"),
                     "evidence": ev["evidence"], "issues": ev["issues"]})

    if args.html:
        _emit_html(report_mod.render(rows), args.html)
        return 0
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
                                directory=_hb_dir(cfg, args.config_path))
        item = {"service": svc["name"], "port": svc.get("port"),
                "level": ev["level_name"], "issues": ev["issues"],
                "heartbeat": hb, "recovery": svc.get("recovery")}
        report["services"].append(item)
        if ev["level"] < 2 or hb["status"] in ("STALE", "FAILING"):
            report["p0"].append(item)
        elif hb["status"] in ("NO_HEARTBEAT", "ALIVE_ONLY", "PROTOCOL_ONLY"):
            report["uncovered"].append(item)

    if args.html:
        _emit_html(report_mod.render(report), args.html)
        return 0
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
                           directory=_hb_dir(cfg, args.config_path))
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
        for n in summary.get("notify") or []:
            channels = n.get("channels") or {}
            print("notify[{}]: {}".format(
                n.get("title"), ", ".join("{}={}".format(k, v) for k, v in channels.items())
                or "no outbound channel configured"))
        if summary.get("notify_error"):
            print("notify error: {}".format(summary["notify_error"]))
        # The DB path stays in --json only: printing it on every cron run is
        # pure noise in a scheduler log.
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


def cmd_history(args):
    cfg = _load(args.config)
    wcfg = cfg.get("watchdog") or {}
    db = history.db_path_for(os.path.dirname(args.config_path), wcfg)
    try:
        since = history.parse_since(args.last)
    except ValueError as exc:
        print(str(exc))
        return 2
    rows = history.query(db, since=since, service=args.service,
                         kind=args.kind, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("no events in window '{}'  (db: {})".format(args.last, db))
        print("run 'fleetkeeper --config FILE sweep' first to record one")
        return 0
    def _stamp(row):
        # Second-resolution timestamps collapse several sweeps into one
        # identical line; milliseconds make the ordering visible.
        ts = float(row["ts"] or 0)
        return (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                + ".{:03d}".format(int(ts * 1000) % 1000))

    print("{:<23} {:<7} {:<9} {:<24} {:<6} {}".format(
        "TIME", "KIND", "LEVEL", "SERVICE", "ISSUES", "MESSAGE"))
    for r in rows:
        print("{:<23} {:<7} {:<9} {:<24} {:<6} {}".format(
            _stamp(r), r["kind"], r["level"] or "-",
            (r["service"] or "fleet")[:24],
            len(r["issues"]), (r["message"] or "")[:60]))
    print()
    print("{} event(s) shown; total in db: {}. '--last all' shows everything.".format(
        len(rows), history.count(db)))
    return 0


def cmd_start(args):
    """Open the local browser console; auto-create the config on first run."""
    from . import ui  # late import: ui imports this module for the ladder logic
    return ui.serve(args.config, port=args.port,
                    open_browser=not args.no_browser)


def cmd_init(args):
    """Create a runnable config without needing a repository checkout."""
    if getattr(args, "list_presets", False):
        for item in template.preset_list():
            print("{:<10} {}".format(item["name"], item["label"]))
            for s in item["services"]:
                print("           {:<16} :{}".format(s["name"], s["port"]))
        return 0

    path = os.path.abspath(args.config)
    if os.path.exists(path) and not args.force:
        print("{} already exists; nothing changed. Use --force to overwrite.".format(path))
        return 1
    try:
        cfg = template.preset_config(args.preset, port=args.port)
    except ValueError as exc:
        print(str(exc))
        return 2
    safeio.atomic_write_text(path, template.render(cfg))
    names = ", ".join(s["name"] for s in cfg["registry"]["services"].values())
    print("wrote {} (preset: {})".format(path, args.preset))
    print("services: {}".format(names))
    print("edit registry.services to match your ports, then:")
    print("  fleetkeeper --config {} doctor --html status.html".format(args.config))
    print("  fleetkeeper --config {} sweep".format(args.config))
    print("net_probe_targets is empty = network gate off; add hosts to enable it.")
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


def _hb_dir(cfg, config_path):
    """Resolve heartbeat_dir relative to the config file, not the CWD.

    Explicit parameter instead of a function attribute: module-level mutable
    state leaks between two configs in the same process (and between tests).
    """
    base = os.path.dirname(os.path.abspath(config_path))
    d = (cfg.get("heartbeat_dir") or "runtime/heartbeat")
    return d if os.path.isabs(d) else os.path.join(base, d)


def build_parser():
    # --config is accepted BOTH before and after the subcommand. Two separate
    # Action objects are required: argparse's `parents=` shares ONE Action
    # instance, and set_defaults() on the parent would then rewrite the
    # child's SUPPRESS default, silently discarding a leading --config.
    child = argparse.ArgumentParser(add_help=False)
    child.add_argument("--config", default=argparse.SUPPRESS,
                       help="fleet config JSON (default: config/fleet.json)")

    p = argparse.ArgumentParser(
        prog="fleetkeeper",
        description="Self-healing watchdog for local AI service fleets: "
                    "network-gated healing, evidence ladder, alerts, timeline.")
    p.add_argument("--config", default="config/fleet.json",
                   help="fleet config JSON (default: config/fleet.json)")
    sub = p.add_subparsers(dest="cmd")

    st = sub.add_parser("start", parents=[child],
                        help="open the local browser console (no commands to learn)")
    st.add_argument("--port", type=int, default=0,
                    help="console port; 0 = pick a free port automatically")
    st.add_argument("--no-browser", action="store_true",
                    help="serve without opening a browser (for tests/remote use)")
    st.set_defaults(func=cmd_start)

    ini = sub.add_parser("init", parents=[child],
                         help="write a runnable config template")
    ini.add_argument("--port", type=int, default=template.DEFAULT_PORT,
                     help="service port for the empty/single presets (default: 8080)")
    ini.add_argument("--preset", default="empty",
                     choices=sorted(template.PRESET_LABELS),
                     help="starting scenario (default: empty)")
    ini.add_argument("--list-presets", action="store_true",
                     help="show what each scenario contains and exit")
    ini.add_argument("--force", action="store_true",
                     help="overwrite an existing config file")
    ini.set_defaults(func=cmd_init)

    s = sub.add_parser("status", parents=[child], help="evidence ladder per service")
    s.add_argument("service", nargs="?")
    out = s.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true")
    out.add_argument("--html", nargs="?", const="-", metavar="FILE",
                     help="write a self-contained status page; '-' prints to stdout")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("doctor", parents=[child], help="fleet-wide diagnosis")
    dout = d.add_mutually_exclusive_group()
    dout.add_argument("--json", action="store_true")
    dout.add_argument("--html", nargs="?", const="-", metavar="FILE",
                      help="write a self-contained diagnosis page; '-' prints to stdout")
    d.set_defaults(func=cmd_doctor)

    h = sub.add_parser("heartbeat", parents=[child], help="write one heartbeat")
    h.add_argument("service")
    h.add_argument("--processed", type=int, default=None)
    h.add_argument("--fail", action="store_true")
    h.add_argument("--proof", default=None,
                   choices=["business", "process_alive", "protocol_only"])
    h.set_defaults(func=cmd_heartbeat)

    w = sub.add_parser("sweep", parents=[child],
                       help="run one watchdog sweep (cron entry)")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_sweep)

    w2 = sub.add_parser("watch", parents=[child], help="run sweeps in a loop")
    w2.add_argument("--interval", type=int, default=60)
    w2.set_defaults(func=cmd_watch)

    hs = sub.add_parser("history", parents=[child],
                        help="incident timeline (sqlite event stream)")
    hs.add_argument("--last", default="24h",
                    help="window: 24h / 30m / 7d / 90s / all (default: 24h)")
    hs.add_argument("--service", default=None, help="filter by service name")
    hs.add_argument("--kind", default=None, choices=["sweep", "alert"])
    hs.add_argument("--limit", type=int, default=50)
    hs.add_argument("--json", action="store_true")
    hs.set_defaults(func=cmd_history)

    lg = sub.add_parser("logs", parents=[child],
                        help="show service logs (redacted by default)")
    lg.add_argument("service")
    lg.add_argument("--tail", type=int, default=40)
    lg.add_argument("--raw", action="store_true")
    lg.set_defaults(func=cmd_logs)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # No arguments means "just open it". This is what double-clicking the exe
    # produces, and it is the only path a first-time user ever needs.
    if not argv:
        argv = ["start"]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    # A frozen exe must keep its config next to itself: Explorer usually sets
    # CWD to a system directory the user cannot find (or cannot write to).
    if getattr(sys, "frozen", False) and not any(
            a == "--config" or a.startswith("--config=") for a in argv):
        args.config = os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)), "fleet.json")
    # pass the resolved config path explicitly; no module-level mutable state
    args.config_path = os.path.abspath(args.config)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print("config not found: {} ({})".format(args.config, e))
        print("create one with: fleetkeeper init --config {}".format(args.config))
        return 2


if __name__ == "__main__":
    sys.exit(main())
