import argparse
import asyncio
import uuid

from request_quorum import gather_quorum


async def main(expected_signatures: int):
    result = await gather_quorum(
        request_id=f"fault-{uuid.uuid4().hex[:8]}",
        subject="0xFE3B557E8Fb62b89F4916B721be55cEb828dBd73",
        holder_url="http://localhost:8041",
    )
    actual = result["valid_signatures"]
    print(f"Firmas válidas: {actual}; esperado: {expected_signatures}")
    if actual != expected_signatures:
        raise SystemExit(1)
    print("✅ Resultado de tolerancia a fallos esperado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Comprueba el número de oráculos disponibles")
    parser.add_argument("--expected-signatures", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.expected_signatures))
