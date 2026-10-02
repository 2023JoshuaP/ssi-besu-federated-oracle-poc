# PoC: oráculo federado 2-de-3

Este prototipo utiliza tres verificadores ECDSA y `OracleQuorum.sol` en Besu.
Dos firmas de oráculos distintos autorizan una solicitud; una firma no basta.

## Requisitos

Antes de iniciar el PoC deben estar activos Indy/VON, ACA-Py, Besu y los
contratos del sistema principal. El contrato `OracleQuorum` se despliega aparte
y no modifica `FlightReservation`.

## Ejecución

```bash
cd oracle-federated-poc
docker compose up --build -d
venv/bin/python scripts/test_oracle_quorum.py
scripts/test_fault_tolerance.sh
```

## Carga

El modo predeterminado `VERIFY_MODE=acapy` mide el flujo completo del mock de
verificación contra ACA-Py. Cada autorización genera tres consultas a
`/credentials`; incrementar la concurrencia puede saturar el wallet Askar.

```bash
venv/bin/python scripts/load_test_oracles.py --requests 100 --concurrency 5
venv/bin/python scripts/load_test_oracles.py --requests 1000 --concurrency 10
```

Para aislar capacidad HTTP/firma del cuello de botella ACA-Py, cambiar
temporalmente `VERIFY_MODE=static` en `.env`, recrear los oráculos y ejecutar
la carga. Este modo no es verificación SSI.

```bash
docker compose up --build -d --force-recreate
venv/bin/python scripts/load_test_oracles.py --requests 10000 --concurrency 100
```

Los resultados se guardan en `results/`. No iniciar 100 000 solicitudes antes
de medir 100, 1 000 y 10 000: son 300 000 llamadas HTTP a los verificadores.

Para medir quórum y escritura en Besu se usa un remitente serializado, evitando
colisiones de nonce. Empezar con 10 y luego 100 solicitudes; miles de
transacciones requieren un gestor de nonces y múltiples cuentas financiadas.

```bash
venv/bin/python scripts/load_test_end_to_end.py --requests 10
venv/bin/python scripts/load_test_end_to_end.py --requests 100
```

## Límites

`VERIFY_MODE=acapy` comprueba presencia de credencial y `can_ride=true`; no
verifica una proof presentation AnonCreds ni revocación. El modo `static` solo
sirve para aislar rendimiento del quórum.
