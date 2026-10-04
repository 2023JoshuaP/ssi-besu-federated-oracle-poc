"""Runs A1-A12 exactly ten times and records one JSON object per execution."""
import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from experiment_a_harness import (
    ROOT_DIR,
    ExperimentAHarness,
    base_record,
    new_payload,
    recreate_verifiers,
    request_signatures,
    wait_for_verifiers,
)

CASES = {
    "A1": {"expected": True, "active": [1, 2, 3, 4, 5]},
    "A2": {"expected": True, "active": [1, 2, 3, 4], "stopped": [5]},
    "A3": {"expected": True, "active": [1, 2, 3], "stopped": [4, 5]},
    "A4": {"expected": False, "active": [1, 2], "stopped": [3, 4, 5]},
    "A5": {"expected": True, "active": [1, 2, 3, 4, 5], "scenario": {"VERIFIER_5_DECISION": "reject"}},
    "A6": {"expected": True, "active": [1, 2, 3, 4, 5], "scenario": {"VERIFIER_4_DECISION": "reject", "VERIFIER_5_DECISION": "reject"}},
    "A7": {"expected": False, "active": [1, 2, 3, 4, 5], "scenario": {"VERIFIER_3_DECISION": "reject", "VERIFIER_4_DECISION": "reject", "VERIFIER_5_DECISION": "reject"}},
    "A8": {"expected": True, "active": [1, 2, 3, 4, 5], "scenario": {"VERIFIER_5_SIGNATURE": "invalid"}},
    "A9": {"expected": False, "active": [1, 2, 3, 4, 5], "scenario": {"VERIFIER_3_SIGNATURE": "invalid", "VERIFIER_4_SIGNATURE": "invalid", "VERIFIER_5_SIGNATURE": "invalid"}},
    "A10": {"expected": False, "active": [1, 2, 3, 4, 5], "replay": True},
    "A11": {"expected": False, "active": [1, 2, 3, 4, 5], "expired": True},
    "A12": {"expected": False, "active": [1, 2, 3, 4, 5], "scenario": {"VERIFIER_1_DECISION": "reject", "VERIFIER_2_DECISION": "reject", "VERIFIER_3_DECISION": "reject"}},
}


async def one_run(case_name, repetition, harness):
    case = CASES[case_name]
    scenario = dict(case.get("scenario", {}))
    scenario["RESERVATION_CONTRACT_ADDRESS"] = harness.deployment["reservation"]
    recreate_verifiers(scenario, case.get("stopped", []))
    await wait_for_verifiers(case["active"])

    unique = f"{case_name}-{repetition}-{uuid.uuid4().hex[:8]}"
    # Functional executions can be rerun without redeploying the contracts.
    # Deriving the id from the UUID prevents a previous diagnostic run from
    # colliding with the same case/repetition fixture.
    evtol_id = int.from_bytes(harness.w3.keccak(text=f"evtol-{unique}")[-8:], "big")
    fixture = harness.prepare_fixture(unique, 0, evtol_offset=evtol_id)
    trip_id = f"PAPER-TRIP-{int(time.time())}-{unique}"
    payload = new_payload(harness, fixture, trip_id)
    if case.get("expired"):
        payload["expiration"] = int(time.time()) - 1

    responses = await request_signatures(payload, case["active"])
    signatures = [response["data"]["signature"] for response in responses if response.get("status_code") == 200]
    result = {**fixture, "trip_id": trip_id, "nonce": payload["nonce"], "responses": responses}

    # A10 first consumes the nonce with a valid reservation, then reuses it.
    if case.get("replay"):
        first = await request_signatures(payload, [1, 2, 3, 4, 5])
        first_signatures = [response["data"]["signature"] for response in first if response.get("status_code") == 200]
        harness.submit_reservation(fixture, trip_id, payload["nonce"], payload["expiration"], first_signatures)
        replay_evtol_id = int.from_bytes(harness.w3.keccak(text=f"replay-evtol-{unique}")[-8:], "big")
        fixture = harness.prepare_fixture(f"replay-{unique}", 0, evtol_offset=replay_evtol_id)
        trip_id = f"PAPER-TRIP-REPLAY-{int(time.time())}-{unique}"
        # Intentionally retain nonce; trip and resource differ, proving nonce protection.
        payload = new_payload(harness, fixture, trip_id, nonce=payload["nonce"])
        responses = await request_signatures(payload, case["active"])
        signatures = [response["data"]["signature"] for response in responses if response.get("status_code") == 200]
        result.update(fixture=fixture, trip_id=trip_id, credential_hash=fixture["credential_hash"])

    started = time.perf_counter()
    accepted = False
    receipt = None
    error = None
    calldata = harness.reservation.encodeABI(
        fn_name="createReservation",
        args=[
            trip_id, harness.account.address, fixture["origin"], fixture["destination"], fixture["evtol_id"], True,
            harness.w3.to_bytes(hexstr=fixture["credential_hash"]), harness.w3.to_bytes(hexstr=payload["nonce"]), payload["expiration"],
            [harness.w3.to_bytes(hexstr=signature) for signature in signatures],
        ],
    )
    try:
        receipt = harness.submit_reservation(fixture, trip_id, payload["nonce"], payload["expiration"], signatures)
        accepted = True
    except Exception as exc:
        error = str(exc)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

    record = base_record(case_name, f"{case_name}-{repetition}", len(case["active"]), result)
    record.update({
        "timestamp_end": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "verification_result": {"responses": len(responses), "http_approved": sum(bool(r.get("data", {}).get("approved")) for r in responses)},
        "reservation_result": "accepted" if accepted else "rejected",
        "reservation_latency_ms": elapsed_ms,
        "expected_result": "accepted" if case["expected"] else "rejected",
        "passed": accepted == case["expected"],
    })
    if receipt:
        record.update({
            "transaction_hash": receipt.transactionHash.hex(),
            "transaction_status": int(receipt.status),
            "block_number": receipt.blockNumber,
            "gas_used": receipt.gasUsed,
            "calldata_bytes": (len(calldata) - 2) // 2,
        })
    else:
        record.update({"error_type": "expected_revert" if not case["expected"] else "unexpected_error", "error_message": error})
    return record


async def main(case_filter, repetitions):
    harness = ExperimentAHarness()
    output = ROOT_DIR / "results" / "experiment_a_functional.jsonl"
    output.parent.mkdir(exist_ok=True)
    cases = [case_filter] if case_filter else list(CASES)
    all_passed = True
    with output.open("a") as result_file:
        for case_name in cases:
            for repetition in range(1, repetitions + 1):
                print(f"Ejecutando {case_name}, repetición {repetition}/{repetitions}")
                record = await one_run(case_name, repetition, harness)
                result_file.write(json.dumps(record) + "\n")
                result_file.flush()
                all_passed = all_passed and record["passed"]
                print("✅" if record["passed"] else "❌", record["reservation_result"])
    if not all_passed:
        raise SystemExit("Al menos una ejecución no alcanzó el resultado esperado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Protocolo funcional A1-A12")
    parser.add_argument("--case", choices=CASES.keys())
    parser.add_argument("--repetitions", type=int, default=10)
    arguments = parser.parse_args()
    if arguments.repetitions < 1:
        parser.error("--repetitions debe ser positivo")
    asyncio.run(main(arguments.case, arguments.repetitions))
