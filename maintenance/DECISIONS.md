# Decisiones vigentes

Este archivo conserva únicamente decisiones aplicables al código actual. Cuando una
se sustituye, actualizar su descripción y conservar el motivo histórico en Git o en un
archivo cerrado. No acumular versiones contradictorias como reglas simultáneas.

## Separación de responsabilidades

Core orquesta; drivers traducen políticas sin BD ni API entrante; servicios atienden
HTTP; gateway posee nftables y WireGuard. Un solo worker y escritor de reconciliación.
Esto limita dependencias de hardware y permite probar cada frontera.

## Estado completo y observación

Una política de salida por instalación. Aplicar y observar estado remoto antes de
confirmar éxito; estado desconocido no equivale a conjunto vacío (`GET /api/v1/routes/active`
devuelve 502 saneado si falla la observación). Pausa/purga deben retirar o desactivar lo
gestionado y comprobarlo. UniFi administra ALL_CLIENTS, preserva estados explícitamente
deshabilitados (`is_active=false`), soporta `unifi_kill_switch` opcional (`false` por defecto,
visibilizado en estado y prueba de conexión) y exige selección explícita cuando existen varias VPN.
OPNsense valida acción/dirección/alias/gateway de su regla y MikroTik exige tabla FIB.
La configuración candidata y la aplicada se separan: `test-router` y la detección de VPN
evalúan candidatos en memoria sin persistir ni sincronizar, mientras el motor registra
qué identidad puede poseer la política remota. Cambios de identidad retiran primero la política
anterior y fallo de retirada rechaza el cambio; un candidato incompleto que nunca aplicó
puede corregirse sin retirada y abandonar un estado desconocido exige confirmación
explícita y visible. Router y SQLite no forman una transacción distribuida.

## Control de concurrencia y presupuestos de ciclo

Las reconciliaciones solicitadas desde servicios se canalizan a través del supervisor de
`app/core/runtime.py` (referencias fuertes, coalescing por ciclo y drenado ordenado antes de
cerrar drivers en el apagado). La adquisición de feeds (`feed_concurrency=4`, `feed_budget_seconds=30s`) y la
resolución DNS (`dns_concurrency=16`, `dns_budget_seconds=20s`) corren fuera de
`ROUTING_LOCK` con cancelación cooperativa por versión de política. Pausa, purga,
cambios de ajustes y WireGuard adquieren `ROUTING_LOCK` con prioridad sobre nuevas
reconciliaciones; ninguna política calculada antes de un cambio o pausa se aplica tras
revalidar versión dentro del bloqueo. Las escrituras remotas ya iniciadas no se cancelan
a ciegas: esperan finalización ordenada y registran estado parcial si son interrumpidas.

## Persistencia y contribuciones

SQLite asíncrono con WAL, `busy_timeout=5000`, claves persistentes y secretos cifrados.
Los destinos manuales (`feed_id IS NULL`) tienen unicidad global (`uq_targets_manual_value`,
HTTP 409) independientemente de la carpeta; los destinos de feeds mantienen unión efectiva
con los manuales. Cada `Target` FQDN registra `dns_status`/`dns_error` y `/api/v1/status`
expone `dns_errors` y `uncovered_targets` (degradando salud cuando un objetivo activo
queda sin IPs). Las resoluciones DNS en `resolved_ips` tienen restricción única
`(target_id, ip_address)` y se actualizan de forma atómica en `resolution_store.py` sin
acortar retención ni fallar si un target se elimina concurrentemente. Los destinos de un
feed conservan identidad y retención DNS si siguen presentes. El error de una fuente
(incluido filtro de operador inválido) conserva snapshot, `etag` y targets previos y
muestra degradación. Manuales y feeds habilitados contribuyen a la misma política;
señal/horario comunitarios no deshabilitan destinos manuales. La retención efectiva de
una resolución es max(TTL sintético, ventana configurada).

## Capacidades e integración local

Capacidades declaradas en drivers/capabilities validan ingesta y cambio de driver.
Gateway, MikroTik y Omada son IPv4. No permitir combinaciones incompatibles para luego
fallar al aplicar todo el conjunto. Gateway solo por socket Unix sobre interfaz fija `wg0`
y volumen compartido con la aplicación; no hay sidecar remoto ni acceso Docker desde el panel.

## WireGuard y confirmación de protección

El sidecar comprueba regla de marca/tabla y ruta de protección; la aplicación exige ACK
verificado y política coincidente. Aplicación y sidecar se actualizan juntos.
Cada reemplazo guarda última configuración aplicada (0600); fallo intenta restaurar
archivo, túnel y ruta, y comunica si falla también el rollback. Una candidata guardada
no acredita que esté aplicada ni que haya conectividad de clientes.

## Sesiones y despliegue

Credencial administrativa local; API Bearer y sesión web firmada con origen validado.
El proxy conserva Host; las sesiones no requieren confianza global de cabeceras.
Distribución con imágenes y Compose fijados a versión, assets locales y sin Node en runtime.
Validar descarga anónima por separado de una CI autenticada antes de publicar instalación pública.
