"""Command line entry point for the NSE disclosed-trade study."""

import argparse
from datetime import date, timedelta

from impact import gates, pipeline
from impact.analysis import report, truncation_sim
from impact.config import Settings
from impact.ingest import nse_client
from impact.ingest.readers import read_deals


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="impact", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="download raw NSE files (idempotent, write-once)")
    fetch.add_argument("target", choices=("deals", "events", "master", "live", "daily"))
    fetch.add_argument("--source", choices=sorted(nse_client.DAILY), help="daily source (default: all)")
    fetch.add_argument("--from-year", type=int)
    fetch.add_argument("--to-year", type=int)
    fetch.add_argument("--dates", nargs="+", type=date.fromisoformat, help="daily: exactly these days (weekend sessions)")
    check = sub.add_parser(
        "check", help="milestone gates: m0 raw store; m1 clean panel; m2 census; m3 replication; m4 typing audit"
    )
    check.add_argument("milestone", nargs="?", choices=("m0", "m1", "m2", "m3", "m4"), default="m0")
    sub.add_parser("build", help="raw store to parquet: prices, flows, daily panel, benchmarks")
    confirm = sub.add_parser("report", help="confirmatory run: v1 buys (tag spec-v1) or v2 sells and pooled (tag spec-v2)")
    confirm.add_argument("--spec", choices=("v1", "v2", "v3"), default="v1")
    sub.add_parser("simulate", help="D4 gross-trigger truncation simulation: d4_simulation.csv")
    sub.add_parser("sensitivity", help="re-run both tests without the hand dictionary: typing_sensitivity.csv")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.command == "build":
        pipeline.build(settings.raw_dir, settings.data_dir / "parquet")
        return
    if args.command == "simulate":
        dest = settings.data_dir / "out"
        dest.mkdir(parents=True, exist_ok=True)
        runs = truncation_sim.run(truncation_sim.SCENARIOS, range(10))
        runs.write_csv(dest / "d4_simulation.csv")
        print(truncation_sim.summary(runs))
        return
    if args.command == "sensitivity":
        dest = settings.data_dir / "out"
        dest.mkdir(parents=True, exist_ok=True)
        rows = report.typing_sensitivity(settings.raw_dir, settings.data_dir / "parquet")
        rows.write_csv(dest / "typing_sensitivity.csv")
        print(rows)
        return
    if args.command == "report":
        out, dest = settings.data_dir / "parquet", settings.data_dir / "out"
        if args.spec == "v3":
            report.write_outputs_v3(report.run_v3(out), dest)
        elif args.spec == "v2":
            report.write_outputs_v2(report.run_v2(out), dest)
        else:
            report.write_outputs(report.run(out), dest)
        return
    if args.command == "check":
        out = settings.data_dir / "parquet"
        lines = {  # not `report`: that name is the module used by the report command above
            "m0": lambda: gates.m0(settings.raw_dir),
            "m1": lambda: gates.m1(settings.raw_dir, out),
            "m2": lambda: gates.m2(settings.raw_dir, out),
            "m3": lambda: gates.m3(out),
            "m4": lambda: gates.m4(out),
        }[args.milestone]()
        print("\n".join(lines))
        raise SystemExit(1 if any(line.startswith("FAIL") for line in lines) else 0)

    # Stop at yesterday: today's files publish in the evening, and a premature 404 is cached as missing.
    today = date.today()
    until = today - timedelta(days=1)
    store = nse_client.RawStore(settings.raw_dir)
    with nse_client.NseClient(settings) as client:
        if args.target == "deals":
            for kind in nse_client.DEAL_KINDS:  # bulk first: a failure in a later kind cannot block it
                for year in range(args.from_year or 2004, (args.to_year or until.year) + 1):
                    for start, end in nse_client.deal_windows(year, until):
                        nse_client.fetch_deal_window(client, store, kind, start, end)
                    print(f"{kind} {year} done", flush=True)
        elif args.target == "events":
            failed = []
            for name in nse_client.EVENTS:  # one slow source must not starve the others; failures still exit 1
                try:
                    nse_client.fetch_events(client, store, name, until)
                    print(f"events {name} done", flush=True)
                except nse_client.NseError as exc:
                    failed.append(name)
                    print(f"events {name} FAILED: {exc}", flush=True)
            if failed:
                raise SystemExit(f"events incomplete, rerun to resume: {', '.join(failed)}")
        elif args.target == "master":
            nse_client.fetch_master(client, store, today)
        elif args.target == "live":
            snapshot = nse_client.fetch_live_snapshot(client, store, today)
            if snapshot is None:
                raise SystemExit("bulk.csv not available")
            trade_date = read_deals(snapshot)["date"].max()
            nse_client.fetch_deal_window(client, store, "bulk_deals", trade_date, trade_date, folder="live")
            print(f"live snapshot and API pull for {trade_date}")
        else:
            for name in [args.source] if args.source else sorted(nse_client.DAILY):
                if args.dates:
                    print(f"{name} {len(args.dates)} dates: {nse_client.fetch_days(client, store, name, args.dates)} files", flush=True)
                    continue
                src = nse_client.DAILY[name]
                first = max(src.start.year, args.from_year or src.start.year)
                last = min((src.end or until).year, args.to_year or until.year)
                for year in range(first, last + 1):
                    present = nse_client.fetch_daily(client, store, name, date(year, 1, 1), min(date(year, 12, 31), until))
                    print(f"{name} {year}: {present} files", flush=True)
