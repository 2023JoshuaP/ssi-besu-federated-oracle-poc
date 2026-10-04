# Experimento A — Django distribuido 3-de-5 para SSI → Besu

Implementación reproducible de la hipótesis A del plan del paper: cinco
verificadores Django independientes firman atestaciones ECDSA y el contrato
Besu acepta una reserva únicamente tras comprobar al menos tres firmas válidas.

## Garantías medidas

- Cinco contenedores: `verifier-1` a `verifier-5`, cada uno con clave, endpoint
  (`8101`–`8105`), identificador y log de contenedor propios.
- Firma EIP-191 ligada a `UAM_RESERVATION`, `chainId`, contrato, rider,
  `can_ride`, hash de credencial, `trip_id`, nonce y expiración.
- El contrato filtra y cuenta firmas ECDSA de verificadores autorizados; no
  decide según el número de respuestas HTTP.
- `QuorumFlightReservation.sol` es el mismo bytecode para el baseline 1-de-1 y
  el experimento 3-de-5. Cambian únicamente sus parámetros de constructor.
- Nonce de un solo uso, expiración y protección frente a sustitución de
  atributos por la firma vinculada al payload.

La validación SSI actual consulta la presencia de `user_credential` con
`can_ride=true` en ACA-Py. Es un mock de integración, no una presentación
AnonCreds ni una comprobación criptográfica de revocación.

## Preparación

1. Arrancar Besu, ACA-Py y emitir la credencial de `user1` según la
   documentación del workspace.
2. Crear el archivo local de configuración:

```bash
cd oracle-federated-poc
cp .env.example .env
# Editar .env: configurar BESU_DEPLOYER_PRIVATE_KEY de la red Besu local.
venv/bin/pip install -r requirements.txt
```

3. Compilar y desplegar los dos escenarios aislados:

```bash
cd ../besu-uam-contracts/smart_contracts
npm run compile
node scripts/deploy_experiment_a.js
```

El despliegue guarda las direcciones en `deployed_experiment_a.json` y actualiza
el `.env` local con las direcciones de los contratos 3-de-5 y 1-de-1.

## Pruebas funcionales A1–A12

Ejecuta exactamente diez repeticiones por caso:

```bash
cd ../../oracle-federated-poc
venv/bin/python scripts/run_experiment_a_functional.py --repetitions 10
```

Para una prueba de diagnóstico antes del protocolo completo:

```bash
venv/bin/python scripts/run_experiment_a_functional.py --case A1 --repetitions 1
```

El runner reinicia los contenedores antes de cada repetición y registra un JSON
por ejecución en `results/experiment_a_functional.jsonl`.

## Rendimiento A-P1–A-P4

Cada comando ejecuta tres bloques de 100 reservas. El script despliega estado
limpio antes de cada bloque y mide por separado la preparación de fixtures.

```bash
# Quórum 3-de-5
venv/bin/python scripts/run_experiment_a_performance.py --alternative quorum3of5 --level A-P1
venv/bin/python scripts/run_experiment_a_performance.py --alternative quorum3of5 --level A-P2
venv/bin/python scripts/run_experiment_a_performance.py --alternative quorum3of5 --level A-P3
venv/bin/python scripts/run_experiment_a_performance.py --alternative quorum3of5 --level A-P4

# Baseline Trusted Verifier único, mismo bytecode de reserva
venv/bin/python scripts/run_experiment_a_performance.py --alternative baseline1of1 --level A-P1
venv/bin/python scripts/run_experiment_a_performance.py --alternative baseline1of1 --level A-P2
venv/bin/python scripts/run_experiment_a_performance.py --alternative baseline1of1 --level A-P3
venv/bin/python scripts/run_experiment_a_performance.py --alternative baseline1of1 --level A-P4
```

Los resultados individuales se escriben en
`results/experiment_a_reservations.jsonl`; cada bloque produce además un resumen
con p50, p95, p99, desviación estándar, throughput, errores y gas medio.

> El plan exige origen y destino distintos y prohíbe reutilizar vertiports por
> fixture. Eso implica 200 vertiports para 100 reservas, aunque una línea del
> plan menciona 100. El runner prioriza la regla de no reutilización y registra
> 200 vertiports más 100 eVTOLs antes del cronómetro.

## Límites del experimento

Los cinco verificadores comparten máquina, ACA-Py y wallet. Los fallos inducidos
demuestran tolerancia técnica del protocolo, no independencia institucional ni
organizativa. Nunca usar las claves o la red local de este repositorio en
producción.
