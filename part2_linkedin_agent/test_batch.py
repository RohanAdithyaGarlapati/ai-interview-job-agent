"""Resolve a file of LinkedIn job URLs and report the hit rate.

Usage:
    python test_batch.py [urls_file] [--workers N] [--sequential]

URLs are resolved concurrently by default. The work is almost entirely waiting
on network and on headless-browser renders, so threads overlap it well; each
worker thread gets its own browser (see agent/browser.py) because Playwright's
sync API cannot be shared across threads.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.browser import shutdown as browser_shutdown
from agent.pipeline import resolve_job_source

DEFAULT_WORKERS = 5


def parse_args(argv: list[str]) -> tuple[str, int]:
    urls_file, workers = "test_urls.txt", DEFAULT_WORKERS
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--workers":
            i += 1
            workers = int(argv[i])
        elif a == "--sequential":
            workers = 1
        else:
            positional.append(a)
        i += 1
    if positional:
        urls_file = positional[0]
    return urls_file, workers


def resolve_one(url: str) -> dict:
    t0 = time.time()
    try:
        r = resolve_job_source(url).to_dict()
    except Exception as e:
        r = {"linkedin_url": url, "success": False, "error": f"unhandled exception: {e}"}
    r["elapsed_s"] = round(time.time() - t0, 1)
    return r


def main() -> None:
    urls_file, workers = parse_args(sys.argv[1:])
    with open(urls_file) as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    print(f"Resolving {len(urls)} URLs with {workers} worker(s)\n", flush=True)
    wall_start = time.time()
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(resolve_one, u): u for u in urls}
        for n, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            status = "OK  " if r["success"] else "FAIL"
            print(f"[{n}/{len(urls)}] {status} {r['linkedin_url'].rsplit('/', 1)[-1][:52]}"
                  f"  ({r['elapsed_s']}s)", flush=True)

    wall = time.time() - wall_start
    # Keep the report in the input's order rather than completion order.
    order = {u: i for i, u in enumerate(urls)}
    results.sort(key=lambda r: order[r["linkedin_url"]])

    successes = sum(1 for r in results if r["success"])
    cpu_time = sum(r["elapsed_s"] for r in results)
    print("\n" + "=" * 70)
    print(f"Success rate : {successes}/{len(results)} = {100*successes/len(results):.0f}%")
    print(f"Wall clock   : {wall:.1f}s")
    print(f"Summed work  : {cpu_time:.1f}s  (speedup {cpu_time/wall:.1f}x)")
    print(f"Mean per URL : {cpu_time/len(results):.1f}s")
    print("=" * 70)
    for r in results:
        print(f"{'PASS' if r['success'] else 'FAIL':4} | company={r.get('company_name')!r} "
              f"ats={r.get('ats_detected')}")
        print(f"     -> {r.get('final_url') or r.get('error')}")

    with open("test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nFull results written to test_results.json")

    browser_shutdown()


if __name__ == "__main__":
    main()
