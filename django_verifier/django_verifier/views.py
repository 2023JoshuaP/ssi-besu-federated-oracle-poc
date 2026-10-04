"""Django verifier used exclusively by experiment A.

It validates the same ACA-Py credential predicate in every node and signs an
EIP-191 authorization that the reservation contract verifies on-chain.
"""
import json
import os
import time

import httpx
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

DOMAIN = "UAM_RESERVATION"
PRIVATE_KEY = os.environ["PRIVATE_KEY"]
VERIFIER_ID = os.getenv("VERIFIER_ID", "verifier-local")
CHAIN_ID = int(os.getenv("BESU_CHAIN_ID", "1337"))
CONTRACT_ADDRESS = os.environ["RESERVATION_CONTRACT_ADDRESS"]
VERIFY_MODE = os.getenv("VERIFY_MODE", "acapy").lower()
FORCE_DECISION = os.getenv("FORCE_DECISION", "normal").lower()
SIGNATURE_MODE = os.getenv("SIGNATURE_MODE", "valid").lower()

w3 = Web3()
account = Account.from_key(PRIVATE_KEY)


def credential_is_valid(holder_url: str, schema_name: str) -> bool:
    if VERIFY_MODE == "static":
        return schema_name == "user_credential"
    if VERIFY_MODE != "acapy":
        raise ValueError(f"VERIFY_MODE inválido: {VERIFY_MODE}")

    response = httpx.get(f"{holder_url}/credentials", timeout=5.0)
    if response.status_code != 200:
        return False
    for credential in response.json().get("results", []):
        attrs = credential.get("attrs", {})
        if schema_name in str(credential.get("schema_id", "")) and str(
            attrs.get("can_ride", "")
        ).lower() in {"true", "1", "yes"}:
            return True
    return False


def authorization_digest(payload: dict, approved: bool) -> bytes:
    """Must remain byte-for-byte equivalent to QuorumFlightReservation.sol."""
    return w3.keccak(
        w3.codec.encode(
            [
                "bytes32", "uint256", "address", "address", "bool", "bytes32",
                "bytes32", "bytes32", "uint256",
            ],
            [
                w3.keccak(text=DOMAIN),
                CHAIN_ID,
                Web3.to_checksum_address(CONTRACT_ADDRESS),
                Web3.to_checksum_address(payload["rider"]),
                approved,
                Web3.to_bytes(hexstr=payload["credential_hash"]),
                w3.keccak(text=payload["trip_id"]),
                Web3.to_bytes(hexstr=payload["nonce"]),
                int(payload["expiration"]),
            ],
        )
    )


@require_GET
def status(_request):
    return JsonResponse(
        {
            "status": "ok",
            "verifier_id": VERIFIER_ID,
            "verifier_address": account.address,
            "verify_mode": VERIFY_MODE,
            "force_decision": FORCE_DECISION,
            "signature_mode": SIGNATURE_MODE,
        }
    )


@csrf_exempt
@require_POST
def verify(request):
    try:
        payload = json.loads(request.body)
        required = {
            "rider", "holder_url", "schema_name", "credential_hash", "trip_id",
            "nonce", "expiration",
        }
        missing = sorted(required.difference(payload))
        if missing:
            return JsonResponse({"error": f"Campos faltantes: {', '.join(missing)}"}, status=400)
        if int(payload["expiration"]) <= int(time.time()):
            return JsonResponse({"error": "Atestación expirada"}, status=400)

        approved = credential_is_valid(payload["holder_url"], payload["schema_name"])
        if FORCE_DECISION == "reject":
            approved = False
        elif FORCE_DECISION != "normal":
            return JsonResponse({"error": f"FORCE_DECISION inválido: {FORCE_DECISION}"}, status=500)

        signature = account.sign_message(encode_defunct(authorization_digest(payload, approved))).signature.hex()
        if SIGNATURE_MODE == "invalid":
            signature = signature[:-1] + ("0" if signature[-1] != "0" else "1")
        elif SIGNATURE_MODE != "valid":
            return JsonResponse({"error": f"SIGNATURE_MODE inválido: {SIGNATURE_MODE}"}, status=500)

        return JsonResponse(
            {
                "verifier_id": VERIFIER_ID,
                "verifier_address": account.address,
                "approved": approved,
                "rider": payload["rider"],
                "credential_hash": payload["credential_hash"],
                "trip_id": payload["trip_id"],
                "nonce": payload["nonce"],
                "expiration": int(payload["expiration"]),
                "signature": signature,
                "credential_check": "static" if VERIFY_MODE == "static" else "acapy_presence_can_ride",
            }
        )
    except (ValueError, KeyError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    except Exception as error:  # Logs remain per container through gunicorn stderr.
        print(f"[{VERIFIER_ID}] verification error: {error}", flush=True)
        return JsonResponse({"error": "Error de verificación"}, status=503)
