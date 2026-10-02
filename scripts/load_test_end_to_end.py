import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path

from request_quorum import gather_quorum
from test_oracle_quorum import send_quorum_tx

ROOT_DIR = Path(__file__).resolve().parents[1]
SUBJECT = "0xFE3B557E8Fb62b89F4916B721be55cEb828dBd73"
HOLDER_URL = "http://localhost:8041"
POLICY_VERSION = "oracle-poc-v1"


async def run_load(total_requests):
    """Carga completa serializada para evitar colisiones de nonce del remitente Besu."""
    oracle_latencies = []
    tx_latencies = []
    gas_values = []
    successful = 0
    failed = 0
    started = time.perf_counter()

    for index in range(total_requests):
        request_id = f"e2e-{index}-{uuid.uuid4().hex[:8]}"
        oracle_started = time.perf_counter()
        quorum = await gather_quorum(request_id, SUBJECT, HOLDER_URL)
        oracle_latencies.append((time.perf_counter() - oracle_started) * 1000)

        if not quorum["approved"]:
            failed += 1
            continue

        try:
            receipt, tx_time_ms = send_quorum_tx(
                request_id,
                SUBJECT,
                True,
                quorum["expires_at"],
                POLICY_VERSION,
                quorum["signatures"],
            )
            if receipt.status != 1:
                raise RuntimeError("receipt.status != 1")
            successful += 1
            tx_latencies.append(tx_time_ms)
            gas_values.append(int(receipt.gasUsed))
        except Exception as error:
            print(f"Solicitud {index} falló: {error}")
            failed += 1

    elapsed = time.perf_counter() - started
    return {
        "mode": "end_to_end_serialized",
        "requests": total_requests,
        "successful": successful,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(total_requests / elapsed, 3) if elapsed else 0,
        "oracle_latency_ms": {
            "mean": round(statistics.mean(oracle_latencies), 3) if oracle_latencies else 0,
            "p95": round(sorted(oracle_latencies)[max(0, round(0.95 * len(oracle_latencies)) - 1)], 3) if oracle_latencies else 0,
        },
        "besu_transaction_latency_ms": {
            "mean": round(statistics.mean(tx_latencies), 3) if tx_latencies else 0,
            "p95": round(sorted(tx_latencies)[max(0, round(0.95 * len(tx_latencies)) - 1)], 3) if tx_latencies else 0,
        },
        "gas": {
            "mean": round(statistics.mean(gas_values), 3) if gas_values else 0,
            "total": sum(gas_values),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carga completa quórum + transacción Besu")
    parser.add_argument("--requests", type=int, default=10)
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("requests debe ser positivo")

    result = asyncio.run(run_load(args.requests))
    output_dir = ROOT_DIR / "results"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"load_e2e_{args.requests}.json"
    output_file.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"Resultados guardados en {output_file}")
