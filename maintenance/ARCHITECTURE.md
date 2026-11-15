# Arquitectura actual — MVP 0.2

## Componentes

- `app/api`: adaptadores HTTP. CRUD de destinos, feeds y carpetas delega en servicios
  `app/core/*_service.py`, con errores independientes del transporte.
- `app/core/runtime.py`: motor único del proceso. Cambios de ajustes, pausa, purga y
  carga WireGuard adquieren `ROUTING_LOCK` con prioridad (`acquire_control_lock`) y
  abordan adquisiciones en curso. Un solo worker soportado.
- `engine.py` / `resolution_store.py`: la fase de adquisición (refresco concurrente de
  feeds y resolución FQDN con concurrencia y presupuesto total acotados, más upsert
  atómico de `resolved_ips` en `resolution_store.py`) se ejecuta fuera de `ROUTING_LOCK`.
  Antes de aplicar en el driver dentro de `ROUTING_LOCK`, el motor revalida pausa y
  versión monotónica de política para no reaplicar resultados previos a un cambio. No
  maneja APIs concretas de routers ni comandos de red.
- `community.py`: snapshot con señal global y destinos recientes separados, mediante
  funciones de stopliga fijadas a un commit. El ISP limita destinos, no redefine la señal.
- `feed_http.py` / `feeds_sync.py`: descarga acotada a 16 MiB, concurrencia limitada y
  presupuesto total por ciclo; serializa refrescos de un mismo feed con `feed_lock` sin
  retener `ROUTING_LOCK`. Un fallo conserva el snapshot anterior y registra error. HTTP
  304 no rejuvenece la fecha de origen. No se permite fallback de JSON a texto/HTML.
- `schedule.py` / `notifications.py`: horarios solo para contribuciones comunitarias y
  webhook opcional de transiciones, con deduplicación persistida y reintento por ciclo.
- `drivers`: implementan `BaseRouterDriver`; no consultan la BD. Omada/OPNsense aíslan
  clientes síncronos upstream en threads y esperan su finalización incluso al cancelar.
- `gateway`: proceso independiente con control Unix, validación de prefijos, mutaciones
  nft transaccionales y ciclo de vida WireGuard. Comparte volumen con la aplicación.

## Política y contratos

`apply_policy(targets, enabled)` expresa el estado completo deseado. El backend debe
observar el router, aplicar cambios y comprobar el resultado. `SyncResult.success=false`
no equivale a estado vacío: `total_active=-1` indica observación desconocida.
`teardown()` debe confirmar la desactivación o eliminación de lo gestionado, o fallar.
En Omada/OPNsense desactiva la regla y puede conservar grupos/alias para reutilizarlos.

Todos los destinos manuales habilitados contribuyen independientemente de la señal
comunitaria. Un feed contribuye cuando está habilitado, su último estado válido permite
activación y el horario está abierto. Un feed genérico no tiene señal global. La pausa
master retira todas las contribuciones. La opción forzada ignora la señal comunitaria,
pero mantiene validación de frescura y selección de entradas recientes activas.

El driver recibe la unión de CIDRs normalizados/colapsados: una carpeta no es una
política distinta. IPv6 se rechaza al seleccionar gateway/MikroTik/Omada. La instancia de
router queda fija durante un ciclo. Elegir un driver nuevo valida sus requisitos mínimos;
completar uno ya seleccionado es una reparación permitida. El motor persiste qué identidad
de configuración puede poseer la política remota: cambios de identidad con propiedad
registrada retiran primero la política anterior y, si falla la retirada, se rechaza el
cambio. Sin propiedad registrada (nunca aplicó) el cambio es directo; con estado remoto
desconocido se exige confirmación explícita y queda marcado `unverified`. No existe una
transacción distribuida entre router y SQLite: un fallo posterior de persistencia exige
reconciliar.

## Persistencia

SQLite WAL + `synchronous=NORMAL` + `foreign_keys=ON` + `busy_timeout=5000`. Tablas:
ajustes cifrados, feeds, carpetas, destinos, resoluciones, eventos y estado interno de
notificaciones. `resolved_ips` impone unicidad en `(target_id, ip_address)` (migración
aditiva deduplicando registros previos) y `resolution_store.py` realiza upserts atómicos
sin acortar `expires_at` ni `last_seen_at`, tolerando borrados concurrentes de targets.
Las migraciones aditivas incorporan columnas de estado e índices en bases existentes. La
retención DNS se restaura de `resolved_ips`; se purgan expirados durante
reconciliación. El resolver nativo usa max(60 segundos de TTL sintético, ventana
configurada), no el TTL autoritativo.

Feed guarda señal, fecha de origen, última consulta, ETag y último error; los targets
persisten el snapshot validado. Un estado desconocido conserva el último válido y degrada
telemetría. No hay caducidad destructiva automática del snapshot si falla Internet.

## Gateway

Paquetes seleccionados reciben marca `0x100` y usan tabla 100. La ruta por `wg0` tiene
métrica 10; una ruta unreachable de métrica 32760 evita fallback por la tabla principal
cuando fail-closed está activo. Tráfico no seleccionado sigue la tabla principal.
Los destinos confirmados se guardan en un checkpoint 0600 y se restauran al reiniciar.
La cadena forward permanece cerrada durante inicialización y se abre después de restaurar
el set y la política. NAT permite usar el sidecar como gateway de clientes LAN. WireGuard se configura con
Table=off, sin hooks ni resolvconf. El controlador serializa comandos de todos los clientes.
No hay transporte TCP, sidecar remoto ni nft local desde la aplicación en este MVP.

## Seguridad y salud

Clave de administración aleatoria en el volumen, modo 0600; API Bearer y sesiones firmadas
HttpOnly/SameSite. El login web valida y firma el origen externo comunicado por el
navegador contra Origin y Host. Las escrituras con esas sesiones exigen el origen firmado;
HTTPS emite cookie Secure sin depender de la confianza de cabeceras del proxy. No hay
registro global por primera visita ni acceso Docker desde la aplicación. No hay CORS abierto. Credenciales de router cifradas con Fernet;
una clave ausente se crea con permisos restrictivos, y una clave inválida o imposible de
persistir provoca error explícito. Guardar la BD sin su clave no permite recuperar secretos.

`/healthz` es liveness HTTP. `/api/v1/status` es telemetría autenticada y refleja último
evento, errores de feeds y salud del driver. Que Docker esté healthy no certifica el túnel
ni el router: los fallos de dependencia se muestran en estado detallado. Los assets son locales.
El build registra su commit; ya no se monta `.git`, evitando anunciar código aún no desplegado.
