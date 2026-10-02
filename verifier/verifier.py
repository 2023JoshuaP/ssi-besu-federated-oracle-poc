import os
import time
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

# Inicializar Web3 y Cuenta
w3 = Web3()
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "0x1111111111111111111111111111111111111111111111111111111111111111")
ORACLE_NAME = os.getenv("ORACLE_NAME", "oracle-local")
BESU_CHAIN_ID = int(os.getenv("BESU_CHAIN_ID", "1337"))
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "0x0000000000000000000000000000000000000000")
VERIFY_MODE = os.getenv("VERIFY_MODE", "acapy").lower()

# Cargar cuenta
account = Account.from_key(PRIVATE_KEY)
public_address = account.address

app = FastAPI(title=f"Verifier Node: {ORACLE_NAME}")

class VerifyRequest(BaseModel):
    request_id: str
    subject: str
    holder_url: str
    schema_name: str
    policy_version: str
    expires_in_seconds: int
    expires_at: int | None = None

class VerifyResponse(BaseModel):
    oracle: str
    oracle_address: str
    approved: bool
    request_id: str
    expires_at: int
    signature: str
    credential_check: str

async def check_credentials_mock(holder_url: str, schema_name: str) -> bool:
    """Mock que consulta al holder de ACA-Py y busca can_ride=true"""
    try:
        # Petición a ACA-Py
        # Nota: ACA-Py usa /credentials o /credential para listar (usualmente /credentials)
        # Aquí hacemos un try/except general.
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{holder_url}/credentials", timeout=5.0)
            if resp.status_code != 200:
                print(f"[{ORACLE_NAME}] Error al consultar {holder_url}/credentials: {resp.status_code}")
                return False
            
            data = resp.json()
            credentials = data.get("results", [])
            for cred in credentials:
                # Comprobar schema
                if schema_name in str(cred.get("schema_id", "")):
                    # Comprobar attrs
                    attrs = cred.get("attrs", {})
                    if str(attrs.get("can_ride", "")).lower() in ["true", "1", "yes"]:
                        return True
                        
            return False
    except Exception as e:
        print(f"[{ORACLE_NAME}] Excepción conectando a ACA-Py: {e}")
        return False

@app.get("/status")
def status():
    return {
        "status": "ok",
        "oracle_name": ORACLE_NAME,
        "public_address": public_address,
        "verify_mode": VERIFY_MODE,
    }

@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    print(f"[{ORACLE_NAME}] Recibida solicitud req_id={req.request_id} subject={req.subject}")
    
    # 1. Chequear ACA-Py, o aceptar estáticamente para pruebas de carga aisladas.
    if VERIFY_MODE == "static":
        is_valid = req.schema_name == "user_credential"
    elif VERIFY_MODE == "acapy":
        is_valid = await check_credentials_mock(req.holder_url, req.schema_name)
    else:
        raise HTTPException(status_code=500, detail=f"VERIFY_MODE inválido: {VERIFY_MODE}")
    
    # 2. Preparar payload on-chain
    approved = is_valid
    expires_at = req.expires_at or (int(time.time()) + req.expires_in_seconds)
    if expires_at <= int(time.time()):
        raise HTTPException(status_code=400, detail="Authorization already expired")
    
    # Simular un bytes32 de request_id (hashing the string if it's not a hex)
    # Por simplicidad, tomamos el keccak del string
    req_id_bytes = w3.keccak(text=req.request_id)
    
    # En Solidity el hash será:
    # keccak256(abi.encode(chainId, contractAddress, requestId, subject, approved, expiresAt, policyHash))
    policy_hash_bytes = w3.keccak(text=req.policy_version)
    
    # Empaquetado ABI (equivalente a abi.encode de Solidity)
    encoded_data = w3.codec.encode(
        ['uint256', 'address', 'bytes32', 'address', 'bool', 'uint256', 'bytes32'],
        [
            BESU_CHAIN_ID,
            Web3.to_checksum_address(CONTRACT_ADDRESS),
            req_id_bytes,
            Web3.to_checksum_address(req.subject),
            approved,
            expires_at,
            policy_hash_bytes
        ]
    )
    
    digest = w3.keccak(encoded_data)
    
    # 3. Firmar usando EIP-191 (Standard Ethereum Message)
    # OpenZeppelin's ECDSA.recover() by default prepends "\x19Ethereum Signed Message:\n32"
    signable_message = encode_defunct(digest)
    signed_message = account.sign_message(signable_message)
    signature_hex = signed_message.signature.hex()
    
    print(f"[{ORACLE_NAME}] Verificación completada. Approved={approved}. Firma generada.")
    
    return VerifyResponse(
        oracle=ORACLE_NAME,
        oracle_address=public_address,
        approved=approved,
        request_id=req_id_bytes.hex(),
        expires_at=expires_at,
        signature=signature_hex,
        credential_check=("static_policy" if VERIFY_MODE == "static" else "mock_presence_and_can_ride") if approved else "failed"
    )

if __name__ == "__main__":
    import uvicorn
    # Puerto por defecto 8100, pero se puede sobreescribir
    port = int(os.getenv("PORT", "8100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
