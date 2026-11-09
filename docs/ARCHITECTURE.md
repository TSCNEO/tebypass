# Arquitectura actual — MVP 0.2

## Componentes

- `app/api`: adaptadores HTTP. CRUD de destinos, feeds y carpetas delega en servicios
  `app/core/*_service.py`, con errores independientes del transporte.
- `app/core/runtime.py`: motor único del proceso. Reconciliación, cambios de ajustes y
  purga coordinan sus escrituras con `ROUTING_LOCK`. Un solo worker soportado.
- `engine.py`: obtiene contribuciones, resuelve FQDN, conserva registros vigentes,
  colapsa prefijos solapados y aplica una política completa al driver. No maneja APIs
  concretas de routers ni comandos de red.
- `community.py`: snapshot con señal global y destinos recientes separados, mediante
  funciones de stopliga fijadas a un commit. El ISP limita destinos, no redefine la señal.
- `feed_http.py` / `feeds_sync.py`: descarga acotada a 16 MiB y sustitución del snapshot
  validado. Un fallo conserva el anterior y registra error. HTTP 304 no rejuvenece la
  fecha de origen. No se permite fallback de JSON a texto/HTML.
- `schedule.py` / `notifications.py`: horarios solo para contribuciones de feeds y
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
política distinta. IPv6 se rechaza al seleccionar gateway/MikroTik. La instancia de
router queda fija durante un ciclo. Cambios de identidad de salida retiran primero la
política anterior; si falla la retirada, el cambio se rechaza. No existe una transacción
distribuida entre router y SQLite: un fallo posterior de persistencia exige reconciliar.

## Persistencia

SQLite WAL + synchronous NORMAL + foreign_keys ON. Tablas: ajustes cifrados, feeds,
carpetas, destinos, resoluciones, eventos y estado interno de notificaciones. Las
migraciones aditivas incorporan columnas de estado a feeds existentes. La retención DNS
se restaura de `resolved_ips`; se purgan expirados durante reconciliación. El resolver
nativo usa un TTL sintético de 60 segundos más la ventana configurada, no el TTL autoritativo.

Feed guarda señal, fecha de origen, última consulta, ETag y último error; los targets
persisten el snapshot validado. Un estado desconocido conserva el último válido y degrada
telemetría. No hay caducidad destructiva automática del snapshot si falla Internet.

## Gateway

Paquetes seleccionados reciben marca `0x100` y usan tabla 100. La ruta por `wg0` tiene
métrica 10; una ruta unreachable de métrica 32760 evita fallback por la tabla principal
cuando fail-closed está activo. Tráfico no seleccionado sigue la tabla principal.
NAT permite usar el sidecar como gateway de clientes LAN. WireGuard se configura con
Table=off, sin hooks ni resolvconf. El controlador serializa comandos de todos los clientes.
No hay transporte TCP, sidecar remoto ni nft local desde la aplicación en este MVP.

## Seguridad y salud

Clave de administración aleatoria en el volumen, modo 0600; API Bearer y sesiones firmadas
HttpOnly/SameSite. No hay CORS abierto. Credenciales de router cifradas con Fernet;
una clave ausente se crea con permisos restrictivos, y una clave inválida o imposible de
persistir provoca error explícito. Guardar la BD sin su clave no permite recuperar secretos.

`/healthz` es liveness HTTP. `/api/v1/status` es telemetría autenticada y refleja último
evento, errores de feeds y salud del driver. Que Docker esté healthy no certifica el túnel
ni el router: los fallos de dependencia se muestran en estado detallado. Los assets son locales.
El build registra su commit; ya no se monta `.git`, evitando anunciar código aún no desplegado.
