"""Shared helpers for the reproducible 3-of-5 Django experiment (hypothesis A)."""
import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

ROOT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = ROOT_DIR.parent
SMART_CONTRACTS = WORKSPACE_DIR / "besu-uam-contracts" / "smart_contracts"
load_dotenv(ROOT_DIR / ".env")

VERIFIER_URLS = [f"http://localhost:{port}/verify" for port in range(8101, 8106)]
STATUS_URLS = [url.replace("/verify", "/status") for url in VERIFIER_URLS]


class ExperimentAHarness:
    def __init__(self, alternative="quorum3of5"):
        self.alternative = alternative
        self.w3 = Web3(Web3.HTTPProvider(os.getenv("BESU_RPC", "http://localhost:8545")))
        self.account = Account.from_key(os.environ["BESU_DEPLOYER_PRIVATE_KEY"])
        deployments = json.loads((SMART_CONTRACTS / "deployed_experiment_a.json").read_text())
        self.deployment = deployments[alternative]
        self.reservation = self._contract("QuorumFlightReservation", self.deployment["reservation"])
        self.vertiport = self._contract("VertiportManagement", self.deployment["vertiport"])
        self.evtol = self._contract("EVTOLManagement", self.deployment["evtol"])
        self.fixture_loader = self._contract("ExperimentFixtureLoader", self.deployment["fixtureLoader"])

    def _contract(self, name, address):
        abi = json.loads((SMART_CONTRACTS / "contracts" / f"{name}.json").read_text())["abi"]
        return self.w3.eth.contract(address=self.w3.to_checksum_address(address), abi=abi)

    def send(self, contract_function, gas_limit=3_000_000):
        tx = contract_function.build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": gas_limit,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"Transacción revertida: {tx_hash.hex()}")
        return receipt

    def prepare_fixture(self, block_id, fixture_index, evtol_offset=1000):
        """Registers isolated origin, destination and eVTOL before timing a reservation."""
        suffix = f"{block_id}-{fixture_index}"
        origin = f"VP-ORIGIN-{suffix}"
        destination = f"VP-DEST-{suffix}"
        evtol_id = evtol_offset + fixture_index
        credential = b"experiment-a-fixture"
        started = time.perf_counter()
        self.send(self.vertiport.functions.registerVertiport(origin, 1, 2, credential))
        self.send(self.vertiport.functions.registerVertiport(destination, 1, 2, credential))
        self.send(self.evtol.functions.registerEVTOL(evtol_id, origin, credential))
        return {
            "origin": origin,
            "destination": destination,
            "evtol_id": evtol_id,
            "credential_hash": self.w3.keccak(text=f"credential-{suffix}").hex(),
            "fixture_preparation_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def prepare_fixtures_batch(self, block_id, count=100, evtol_offset=1000, batch_size=10):
        """Prepares fixtures in batches outside the measured reservation interval."""
        if count < 1 or batch_size < 1:
            raise ValueError("count y batch_size deben ser positivos")
        credential = b"experiment-a-fixture"
        fixtures = []
        started = time.perf_counter()
        for first in range(0, count, batch_size):
            indexes = range(first, min(first + batch_size, count))
            origins = [f"VP-ORIGIN-{block_id}-{index}" for index in indexes]
            destinations = [f"VP-DEST-{block_id}-{index}" for index in indexes]
            evtol_ids = [evtol_offset + index for index in indexes]
            # Ten fixtures need slightly more than 3M gas because they create
            # thirty storage records. The Besu block limit is ~16M and this
            # preparation transaction is excluded from reservation metrics.
            self.send(
                self.fixture_loader.functions.prepareFixtures(origins, destinations, evtol_ids, credential),
                gas_limit=8_000_000,
            )
            for index, origin, destination, evtol_id in zip(indexes, origins, destinations, evtol_ids):
                suffix = f"{block_id}-{index}"
                fixtures.append({
                    "origin": origin,
                    "destination": destination,
                    "evtol_id": evtol_id,
                    "credential_hash": self.w3.keccak(text=f"credential-{suffix}").hex(),
                })
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        for fixture in fixtures:
            fixture["fixture_preparation_ms"] = elapsed_ms / count
        return fixtures, elapsed_ms

    def submit_reservation(self, fixture, trip_id, nonce, expiration, signatures):
        credential_hash = self.w3.to_bytes(hexstr=fixture["credential_hash"])
        nonce_bytes = self.w3.to_bytes(hexstr=nonce)
        return self.send(self.reservation.functions.createReservation(
            trip_id,
            self.account.address,
            fixture["origin"],
            fixture["destination"],
            fixture["evtol_id"],
            True,
            credential_hash,
            nonce_bytes,
            expiration,
            [self.w3.to_bytes(hexstr=signature) for signature in signatures],
        ))


def write_scenario_env(values):
    """Overrides Compose substitution only; this ignored file never contains a secret."""
    (ROOT_DIR / ".scenario.env").write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")


def recreate_verifiers(scenario=None, stopped=()):
    scenario = scenario or {}
    write_scenario_env(scenario)
    # deploy_experiment_a.js updates .env between blocks, but this Python
    # process loaded the previous value at startup. Compose gives inherited
    # environment variables precedence over env files, so explicitly override
    # them with the freshly deployed contract address for this scenario.
    compose_environment = os.environ.copy()
    compose_environment.update(scenario)
    subprocess.run(
        ["docker", "compose", "--env-file", ".env", "--env-file", ".scenario.env", "up", "--build", "-d", "--force-recreate"],
        cwd=ROOT_DIR,
        check=True,
        env=compose_environment,
    )
    for verifier_id in stopped:
        subprocess.run(["docker", "stop", f"verifier-{verifier_id}"], check=True, stdout=subprocess.DEVNULL)


async def wait_for_verifiers(active_ids, timeout_seconds=45):
    expected = {f"verifier-{number}" for number in active_ids}
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                responses = await asyncio.gather(*[client.get(STATUS_URLS[number - 1], timeout=2) for number in active_ids])
                if {response.json().get("verifier_id") for response in responses if response.status_code == 200} == expected:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise TimeoutError(f"No iniciaron los verificadores {sorted(expected)}")


async def request_signatures(payload, verifier_ids):
    async with httpx.AsyncClient() as client:
        async def one(number):
            started = time.perf_counter()
            try:
                response = await client.post(VERIFIER_URLS[number - 1], json=payload, timeout=10)
                return {
                    "verifier": number,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "status_code": response.status_code,
                    "data": response.json() if response.content else {},
                }
            except httpx.HTTPError as error:
                return {"verifier": number, "latency_ms": round((time.perf_counter() - started) * 1000, 3), "error": str(error)}
        return await asyncio.gather(*(one(number) for number in verifier_ids))


def new_payload(harness, fixture, trip_id, nonce=None, expiration=None):
    return {
        "rider": harness.account.address,
        "holder_url": "http://localhost:8041",
        "schema_name": "user_credential",
        "credential_hash": fixture["credential_hash"],
        "trip_id": trip_id,
        "nonce": nonce or harness.w3.keccak(text=f"nonce-{uuid.uuid4().hex}").hex(),
        "expiration": expiration or int(time.time()) + 120,
    }


def base_record(case, run_id, verifier_count, result):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "experiment_id": "A",
        "alternative": "Django 3-of-5",
        "run_id": run_id,
        "timestamp_start": now,
        "timestamp_end": now,
        "validity_case": case,
        "concurrency": 1,
        "verifier_count": verifier_count,
        "quorum": 3,
        "credential_schema": "user_credential",
        "credential_id_or_hash": result.get("credential_hash"),
        "trip_id": result.get("trip_id"),
        "rpc_endpoint": os.getenv("BESU_RPC", "http://localhost:8545"),
        "chain_id": 1337,
        "transaction_hash": None,
        "transaction_status": None,
        "block_number": None,
        "gas_used": None,
        "calldata_bytes": None,
        "verification_result": None,
        "reservation_result": None,
        "error_type": None,
        "error_message": None,
    }
