"""Reproducible A-P1..A-P4 runner (100 reservations per measured block)."""
import argparse
import asyncio
import json
import statistics
import subprocess
import time
import uuid
from datetime import datetime, timezone

from experiment_a_harness import (
    ROOT_DIR,
    SMART_CONTRACTS,
    ExperimentAHarness,
    new_payload,
    recreate_verifiers,
    request_signatures,
    wait_for_verifiers,
)

LEVELS = {"A-P1": 1, "A-P2": 10, "A-P3": 25, "A-P4": 50}


def percentile(values, value):
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[min(len(ordered) - 1, round((value / 100) * (len(ordered) - 1)))]


def deploy_clean_state():
    """Required to reuse the plan's EVTOL_ID=1000+i at every block."""
    subprocess.run(["node", "scripts/deploy_experiment_a.js"], cwd=SMART_CONTRACTS, check=True)


class ConcurrentSubmitter:
    """Allocates nonces safely while allowing confirmations to overlap on Besu."""
    def __init__(self, harness):
        self.harness = harness
        self.next_nonce = harness.w3.eth.get_transaction_count(harness.account.address)
        self.lock = asyncio.Lock()

    async def submit(self, fixture, payload, signatures):
        function = self.harness.reservation.functions.createReservation(
            payload["trip_id"], self.harness.account.address, fixture["origin"], fixture["destination"], fixture["evtol_id"],
            True, self.harness.w3.to_bytes(hexstr=fixture["credential_hash"]), self.harness.w3.to_bytes(hexstr=payload["nonce"]),
            payload["expiration"], [self.harness.w3.to_bytes(hexstr=sig) for sig in signatures],
        )
        calldata = function._encode_transaction_data()
        async with self.lock:
            tx = function.build_transaction({
                "from": self.harness.account.address,
                "nonce": self.next_nonce,
                "gas": 3_000_000,
                "gasPrice": self.harness.w3.eth.gas_price,
                "chainId": self.harness.w3.eth.chain_id,
            })
            self.next_nonce += 1
            signed = self.harness.account.sign_transaction(tx)
            tx_hash = await asyncio.to_thread(self.harness.w3.eth.send_raw_transaction, signed.rawTransaction)
        receipt = await asyncio.to_thread(self.harness.w3.eth.wait_for_transaction_receipt, tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"Transacción revertida: {tx_hash.hex()}")
        return receipt, (len(calldata) - 2) // 2


async def run_block(alternative, level, block_number, requests=100):
    deploy_clean_state()
    harness = ExperimentAHarness(alternative)
    if alternative == "quorum3of5":
        active_ids, stopped, quorum, label = [1, 2, 3, 4, 5], [], 3, "Django 3-of-5"
    else:
        active_ids, stopped, quorum, label = [1], [2, 3, 4, 5], 1, "Trusted Verifier 1-of-1"
    recreate_verifiers({"RESERVATION_CONTRACT_ADDRESS": harness.deployment["reservation"]}, stopped)
    await wait_for_verifiers(active_ids)

    # Explicitly outside the measured interval.
    fixtures, fixture_preparation_ms = harness.prepare_fixtures_batch(
        f"{alternative}-{level}-{block_number}", count=requests, evtol_offset=1000, batch_size=10
    )

    semaphore = asyncio.Semaphore(LEVELS[level])
    sender = ConcurrentSubmitter(harness)
    records = []
    block_started = time.perf_counter()

    async def reserve(index):
        fixture = fixtures[index]
        payload = new_payload(
            harness,
            fixture,
            trip_id=f"PAPER-TRIP-{level}-{block_number}-{index}-{uuid.uuid4().hex[:8]}",
            expiration=int(time.time()) + 600,
        )
        async with semaphore:
            # The measured latency begins when this reservation is admitted at
            # the configured concurrency level, not while it waits in the
            # client-side scheduling queue.
            started = time.perf_counter()
            responses = await request_signatures(payload, active_ids)
            signatures = [r["data"]["signature"] for r in responses if r.get("status_code") == 200]
            try:
                receipt, calldata_bytes = await sender.submit(fixture, payload, signatures)
                records.append({
                    "experiment_id": "A", "alternative": label, "run_id": f"{level}-{block_number}-{index}",
                    "timestamp_start": datetime.now(timezone.utc).isoformat(), "timestamp_end": datetime.now(timezone.utc).isoformat(),
                    "validity_case": "A1", "concurrency": LEVELS[level], "verifier_count": len(active_ids), "quorum": quorum,
                    "credential_schema": "user_credential", "credential_id_or_hash": fixture["credential_hash"], "trip_id": payload["trip_id"],
                    "rpc_endpoint": harness.w3.provider.endpoint_uri, "chain_id": harness.w3.eth.chain_id,
                    "transaction_hash": receipt.transactionHash.hex(), "transaction_status": int(receipt.status), "block_number": receipt.blockNumber,
                    "gas_used": receipt.gasUsed, "calldata_bytes": calldata_bytes,
                    "verification_result": "approved", "reservation_result": "accepted", "error_type": None, "error_message": None,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "fixture_preparation_ms": fixture["fixture_preparation_ms"],
                })
            except Exception as error:
                records.append({
                    "experiment_id": "A", "alternative": label, "run_id": f"{level}-{block_number}-{index}",
                    "validity_case": "A1", "concurrency": LEVELS[level], "verifier_count": len(active_ids), "quorum": quorum,
                    "trip_id": payload["trip_id"], "reservation_result": "rejected", "error_type": type(error).__name__,
                    "error_message": str(error), "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                })

    await asyncio.gather(*(reserve(index) for index in range(requests)))
    elapsed = time.perf_counter() - block_started
    successful = [record for record in records if record["reservation_result"] == "accepted"]
    latencies = [record["latency_ms"] for record in successful]
    gas = [int(record["gas_used"]) for record in successful]
    summary = {
        "experiment_id": "A", "alternative": label, "level": level, "block": block_number,
        "requests": requests, "concurrency": LEVELS[level], "successful": len(successful), "errors": requests - len(successful),
        "fixture_preparation_ms": fixture_preparation_ms, "measured_elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(len(successful) / elapsed, 4) if elapsed else 0,
        "latency_ms": {"p50": round(percentile(latencies, 50), 3), "p95": round(percentile(latencies, 95), 3), "p99": round(percentile(latencies, 99), 3), "stddev": round(statistics.stdev(latencies), 3) if len(latencies) > 1 else 0},
        "gas_mean": round(statistics.mean(gas), 3) if gas else 0,
    }
    return records, summary


async def main(alternative, level, blocks):
    result_dir = ROOT_DIR / "results"
    result_dir.mkdir(exist_ok=True)
    all_summaries = []
    for block in range(1, blocks + 1):
        print(f"{alternative} {level}: bloque {block}/{blocks}")
        records, summary = await run_block(alternative, level, block)
        with (result_dir / "experiment_a_reservations.jsonl").open("a") as file:
            for record in records:
                file.write(json.dumps(record) + "\n")
        all_summaries.append(summary)
        print(json.dumps(summary, indent=2))
    (result_dir / f"experiment_a_{alternative}_{level}_summary.json").write_text(json.dumps(all_summaries, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bloques de rendimiento A-P1..A-P4")
    parser.add_argument("--alternative", choices=["quorum3of5", "baseline1of1"], required=True)
    parser.add_argument("--level", choices=LEVELS, required=True)
    parser.add_argument("--blocks", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.alternative, args.level, args.blocks))
