import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path

import httpx

from request_quorum import gather_quorum_with_client

ROOT_DIR = Path(__file__).resolve().parents[1]
SUBJECT = "0xFE3B557E8Fb62b89F4916B721be55cEb828dBd73"
HOLDER_URL = "http://localhost:8041"


def percentile(values, percentile_value):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((percentile_value / 100) * (len(ordered) - 1)))
    return ordered[index]


async def run_load(total_requests, concurrency):
    semaphore = asyncio.Semaphore(concurrency)
    latencies = []
    successful = 0
    failed = 0

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=concurrency * 3)) as client:
        async def one_request(index):
            nonlocal successful, failed
            async with semaphore:
                started = time.perf_counter()
                result = await gather_quorum_with_client(
                    client,
                    f"load-{index}-{uuid.uuid4().hex[:8]}",
                    SUBJECT,
                    HOLDER_URL,
                )
                latencies.append((time.perf_counter() - started) * 1000)
                if result["approved"]:
                    successful += 1
                else:
                    failed += 1

        started = time.perf_counter()
        await asyncio.gather(*(one_request(index) for index in range(total_requests)))
        elapsed = time.perf_counter() - started

    return {
        "mode": "oracle_only",
        "requests": total_requests,
        "concurrency": concurrency,
        "successful": successful,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(total_requests / elapsed, 3) if elapsed else 0,
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 3) if latencies else 0,
            "median": round(statistics.median(latencies), 3) if latencies else 0,
            "p95": round(percentile(latencies, 95), 3),
            "p99": round(percentile(latencies, 99), 3),
            "max": round(max(latencies), 3) if latencies else 0,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carga concurrente contra los tres verificadores")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=25)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests y concurrency deben ser positivos")

    result = asyncio.run(run_load(args.requests, args.concurrency))
    output_dir = ROOT_DIR / "results"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"load_oracle_{args.requests}_c{args.concurrency}.json"
    output_file.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"Resultados guardados en {output_file}")
