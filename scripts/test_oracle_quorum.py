import os
import time
import json
import uuid
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from request_quorum import gather_quorum

ROOT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = ROOT_DIR.parent
load_dotenv(ROOT_DIR / ".env")

# Configuracion Besu
BESU_RPC = "http://localhost:8545"
w3 = Web3(Web3.HTTPProvider(BESU_RPC))

# Cuenta que hace las transacciones (deployer de Besu u otra con fondos).
# La clave existe únicamente en .env, que no se versiona.
DEPLOYER_PK = os.getenv("BESU_DEPLOYER_PRIVATE_KEY")
if not DEPLOYER_PK or DEPLOYER_PK.startswith("REPLACE_WITH_"):
    raise RuntimeError("Configura BESU_DEPLOYER_PRIVATE_KEY en oracle-federated-poc/.env")
deployer_account = Account.from_key(DEPLOYER_PK)

CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS", "0x0000000000000000000000000000000000000000")
if CONTRACT_ADDRESS == "0x0000000000000000000000000000000000000000":
    raise RuntimeError("CONTRACT_ADDRESS no está configurada. Ejecuta deploy_oracle_quorum.js primero.")

# Cargar ABI generado por scripts/compile.js
try:
    abi_path = WORKSPACE_DIR / "besu-uam-contracts" / "smart_contracts" / "contracts" / "OracleQuorum.json"
    with abi_path.open() as f:
        contract_json = json.load(f)
        ABI = contract_json["abi"]
except Exception as e:
    print(f"No se encontro ABI en {abi_path}. Ejecuta npm run compile primero.")
    exit(1)

contract = w3.eth.contract(address=w3.to_checksum_address(CONTRACT_ADDRESS), abi=ABI)

def send_quorum_tx(request_id, subject, approved, expires_at, policy_version, signatures):
    start_tx = time.time()
    
    req_bytes = w3.keccak(text=request_id)
    pol_bytes = w3.keccak(text=policy_version)
    subj_checksum = w3.to_checksum_address(subject)
    
    # Convertir signatures string hex a bytes
    sig_bytes = [w3.to_bytes(hexstr=s) for s in signatures]
    
    tx = contract.functions.submitQuorum(
        req_bytes,
        subj_checksum,
        approved,
        expires_at,
        pol_bytes,
        sig_bytes
    ).build_transaction({
        'from': deployer_account.address,
        'nonce': w3.eth.get_transaction_count(deployer_account.address),
        'gas': 3000000,
        'gasPrice': 0
    })
    
    signed_tx = deployer_account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        raise RuntimeError(f"Transacción revertida: {tx_hash.hex()}")
    
    tx_time_ms = int((time.time() - start_tx) * 1000)
    
    return receipt, tx_time_ms


def sign_authorization(private_key, request_id, subject, approved, expires_at, policy_version):
    """Genera una firma EIP-191 sobre el mismo digest que OracleQuorum.sol."""
    encoded = w3.codec.encode(
        ["uint256", "address", "bytes32", "address", "bool", "uint256", "bytes32"],
        [
            w3.eth.chain_id,
            w3.to_checksum_address(CONTRACT_ADDRESS),
            w3.keccak(text=request_id),
            w3.to_checksum_address(subject),
            approved,
            expires_at,
            w3.keccak(text=policy_version),
        ],
    )
    digest = w3.keccak(encoded)
    return Account.from_key(private_key).sign_message(encode_defunct(digest)).signature.hex()


def expect_revert(label, callback):
    try:
        callback()
        print(f"❌ {label}: el contrato aceptó una operación que debía rechazar")
        return "unexpected_approval"
    except Exception as error:
        print(f"✅ {label}: rechazado ({str(error)[:100]})")
        return "rejected"

async def run_tests():
    print(f"=== Iniciando Pruebas de OracleQuorum ===")
    print(f"Contract Address: {CONTRACT_ADDRESS}")
    
    results_log = []
    subject = "0xFE3B557E8Fb62b89F4916B721be55cEb828dBd73"
    holder_url = "http://localhost:8041"
    
    # Caso 1: Autorizacion Valida (2+ firmas)
    print("\n--- Caso 1: Autorizacion Valida ---")
    req_1 = f"req-{uuid.uuid4().hex[:6]}"
    quorum_1 = await gather_quorum(req_1, subject, holder_url)
    
    if quorum_1["approved"]:
        try:
            receipt, tx_time = send_quorum_tx(req_1, subject, True, quorum_1["expires_at"], "oracle-poc-v1", quorum_1["signatures"])
            print(f"✅ Caso 1 Exitoso. TxHash: {receipt.transactionHash.hex()} | Gas Usado: {receipt.gasUsed}")
            quorum_1["besu_transaction_time_ms"] = tx_time
            quorum_1["gas_used"] = receipt.gasUsed
            quorum_1["outcome"] = "approved on-chain"
        except Exception as e:
            print(f"❌ Caso 1 Fallo en TX: {e}")
            quorum_1["outcome"] = f"tx_failed: {e}"
    else:
        print("❌ Caso 1 Fallo: Oraculos no aprobaron (revisa que ACA-Py este corriendo)")
        quorum_1["outcome"] = "oracle_rejection"
        
    results_log.append(quorum_1)

    # Caso 2: una firma no alcanza el quórum 2-de-3.
    print("\n--- Caso 2: Quorum Insuficiente (1 firma) ---")
    req_2 = f"req-{uuid.uuid4().hex[:6]}"
    quorum_2 = await gather_quorum(req_2, subject, holder_url)
    quorum_2["submitted_signatures"] = 1
    quorum_2["outcome"] = expect_revert(
        "Caso 2 — Quórum insuficiente",
        lambda: send_quorum_tx(
            req_2,
            subject,
            True,
            quorum_2["expires_at"],
            "oracle-poc-v1",
            quorum_2["signatures"][:1],
        ),
    )

    results_log.append(quorum_2)

    # Caso 3: la misma firma no puede contar dos veces.
    print("\n--- Caso 3: Firma Duplicada ---")
    req_3 = f"req-{uuid.uuid4().hex[:6]}"
    quorum_3 = await gather_quorum(req_3, subject, holder_url)
    quorum_3["outcome"] = expect_revert(
        "Caso 3 — Firma duplicada",
        lambda: send_quorum_tx(req_3, subject, True, quorum_3["expires_at"], "oracle-poc-v1", [quorum_3["signatures"][0]] * 2),
    )
    results_log.append(quorum_3)

    # Caso 4: una firma de clave que no pertenece al registro debe fallar.
    print("\n--- Caso 4: Firmante No Autorizado ---")
    req_4 = f"req-{uuid.uuid4().hex[:6]}"
    quorum_4 = await gather_quorum(req_4, subject, holder_url)
    fake_signature = sign_authorization("0x" + "4" * 64, req_4, subject, True, quorum_4["expires_at"], "oracle-poc-v1")
    quorum_4["outcome"] = expect_revert(
        "Caso 4 — Firmante no autorizado",
        lambda: send_quorum_tx(req_4, subject, True, quorum_4["expires_at"], "oracle-poc-v1", [quorum_4["signatures"][0], fake_signature]),
    )
    results_log.append(quorum_4)

    # Caso 5: una autorización ya aceptada no puede reutilizarse.
    print("\n--- Caso 5: Replay ---")
    replay = {"request_id": req_1}
    replay["outcome"] = expect_revert(
        "Caso 5 — Replay",
        lambda: send_quorum_tx(req_1, subject, True, quorum_1["expires_at"], "oracle-poc-v1", quorum_1["signatures"]),
    )
    results_log.append(replay)

    # Caso 6: el contrato rechaza firmas correctas pero vencidas.
    print("\n--- Caso 6: Autorización Vencida ---")
    req_6 = f"req-{uuid.uuid4().hex[:6]}"
    expired_at = int(time.time()) - 1
    expired_signatures = [
        sign_authorization(os.environ["ORACLE_1_PRIVATE_KEY"], req_6, subject, True, expired_at, "oracle-poc-v1"),
        sign_authorization(os.environ["ORACLE_2_PRIVATE_KEY"], req_6, subject, True, expired_at, "oracle-poc-v1"),
    ]
    expired = {"request_id": req_6, "expires_at": expired_at}
    expired["outcome"] = expect_revert(
        "Caso 6 — Autorización vencida",
        lambda: send_quorum_tx(req_6, subject, True, expired_at, "oracle-poc-v1", expired_signatures),
    )
    results_log.append(expired)

    # Caso 7: una política que no encuentra credenciales no alcanza quórum off-chain.
    print("\n--- Caso 7: Credencial Ausente ---")
    req_7 = f"req-{uuid.uuid4().hex[:6]}"
    quorum_7 = await gather_quorum(req_7, subject, holder_url, schema_name="schema_inexistente")
    quorum_7["outcome"] = "rejected_off_chain" if not quorum_7["approved"] else "unexpected_approval"
    print("✅ Caso 7: credencial ausente rechazada por los oráculos" if not quorum_7["approved"] else "❌ Caso 7: oráculos aprobaron una credencial ausente")
    results_log.append(quorum_7)

    # Guardar reporte
    results_dir = ROOT_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    with (results_dir / "test_report.json").open("w") as f:
        json.dump(results_log, f, indent=2)
    print("\nReporte guardado en results/test_report.json")

if __name__ == "__main__":
    asyncio.run(run_tests())
