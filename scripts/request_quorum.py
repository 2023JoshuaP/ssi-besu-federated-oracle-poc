import os
import time
import json
import httpx
import asyncio
from web3 import Web3

# Oracles endpoints
ORACLE_URLS = [
    "http://localhost:8101/verify",
    "http://localhost:8102/verify",
    "http://localhost:8103/verify"
]

async def request_signature(client, url, payload):
    start_time = time.time()
    try:
        resp = await client.post(url, json=payload, timeout=10.0)
        elapsed = int((time.time() - start_time) * 1000)
        
        if resp.status_code == 200:
            data = resp.json()
            return {"url": url, "data": data, "error": None, "time_ms": elapsed}
        else:
            return {"url": url, "data": None, "error": f"HTTP {resp.status_code}", "time_ms": elapsed}
    except Exception as e:
        elapsed = int((time.time() - start_time) * 1000)
        return {"url": url, "data": None, "error": str(e), "time_ms": elapsed}

async def gather_quorum_with_client(client, request_id, subject, holder_url, schema_name="user_credential", policy_version="oracle-poc-v1", expires_in=120):
    expires_at = int(time.time()) + expires_in
    payload = {
        "request_id": request_id,
        "subject": subject,
        "holder_url": holder_url,
        "schema_name": schema_name,
        "policy_version": policy_version,
        "expires_in_seconds": expires_in,
        "expires_at": expires_at
    }
    
    start_quorum = time.time()
    
    tasks = [request_signature(client, url, payload) for url in ORACLE_URLS]
    results = await asyncio.gather(*tasks)
        
    quorum_time_ms = int((time.time() - start_quorum) * 1000)
    
    valid_responses = []
    verification_times = {}
    
    for res in results:
        url_name = res["url"].split(":")[2].split("/")[0] # e.g. 8101 -> oracle_1
        verification_times[url_name] = res["time_ms"]
        
        if res["data"] and res["data"]["approved"]:
            valid_responses.append(res["data"])
            
    return {
        "request_id": request_id,
        "oracle_responses": len(results),
        "valid_signatures": len(valid_responses),
        "threshold": 2,
        "verification_times_ms": verification_times,
        "quorum_time_ms": quorum_time_ms,
        "signatures": [r["signature"] for r in valid_responses],
        "expires_at": expires_at,
        "approved": len(valid_responses) >= 2,
        "raw_responses": valid_responses
    }


async def gather_quorum(request_id, subject, holder_url, schema_name="user_credential", policy_version="oracle-poc-v1", expires_in=120):
    async with httpx.AsyncClient() as client:
        return await gather_quorum_with_client(
            client, request_id, subject, holder_url, schema_name, policy_version, expires_in
        )

if __name__ == "__main__":
    # Test simple standalone
    import uuid
    req_id = f"req-{uuid.uuid4().hex[:6]}"
    # Mocking subject with deployer address or any valid 0x
    subj = "0xFE3B557E8Fb62b89F4916B721be55cEb828dBd73" 
    # Asegúrate de tener ACA-Py User1 corriendo en 8041
    print(f"Requesting Quorum for {req_id}...")
    res = asyncio.run(gather_quorum(req_id, subj, "http://localhost:8041"))
    print(json.dumps(res, indent=2))
