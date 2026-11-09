# Ejecución por fases de la auditoría

## Fase 1 — Control fiable

Implementado:
- Apagado gateway con protocolo correcto, comprobación remota y error si no se confirma.
- Eliminación UniFi con comprobación posterior y errores propagados.
- Sin éxito simulado en drivers operativos; MikroTik se declara sin implementar.
- Motor compartido por la API y exclusión de escrituras entre motores, purga y ajustes.
- Driver fijo durante un ciclo y eventos de fallo de observación/apagado.
- Desconocido se representa como `total_active=-1` en resultados fallidos.

Validación local: 83 tests, Ruff y Mypy correctos en Python 3.12. Se corrigió también
el aislamiento de SyncEvent entre pruebas. Usar un basetemp corto para sockets en macOS.
Pruebas HTTP de lectura del despliegue existente: panel, liveness, versión, estado y
OpenAPI responden HTTP 200. No se almacenan datos privados del entorno en el repositorio.

Pendiente: contrato completo de políticas, transición de configuración y telemetría
observada; no se consideran resueltos por serializar las escrituras. Continúan en las
siguientes fases del plan de auditoría.

## Fase 2 — Feed comunitario

Pendiente: snapshot con estado global separado de destinos, frescura, límites y
tratamiento explícito de errores. Ausencia de bloqueo comunitario no afecta a manuales.

## Fases 3–6

Ver prioridades y criterios de aceptación en AUDIT-2026-11-09.md.
