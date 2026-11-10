# Referencia de configuración

Los nombres entre código son las claves de `GET /api/v1/settings` y
`PUT /api/v1/settings`. La tabla cubre todas las claves editables actuales, incluidas
las que no tienen control en el panel. Valores indicados: predeterminados del código,
no necesariamente los de una instalación existente.

## Dónde configurar

- **Panel:** Ajustes → Router, Motor o WireGuard. Pulsar Guardar ajustes para persistir
  antes de cargar WireGuard. Probar conexión también guarda el formulario y puede sincronizar. WireGuard tiene su propio botón Guardar configuración.
- **API:** enviar solo las claves a cambiar mediante PUT. Leer primero el estado.
  La BD prevalece sobre valores de arranque; cambiar una variable de entorno no sustituye
  un ajuste ya persistido. El Compose estándar no reenvía todas las variables de Settings.
- Secretos: GET devuelve `***saved***`. Vacío, `null` o esa máscara conservan el secreto;
  no sirven para borrarlo. Para rotarlo, introducir otro valor. Las cadenas ordinarias
  sí pueden vaciarse, por ejemplo para quitar horario o webhook. Las claves desconocidas
  o no editables se ignoran: comprobar la respuesta y el estado después del PUT.
- Guardar solicita reconciliación en segundo plano; éxito al guardar no equivale a
  aplicación confirmada en el router. Cambiar identidad de router/política exige retirar
  lo anterior; si falla, se rechaza el cambio. No existe atomicidad distribuida con la BD.

## Motor y fuentes

| Clave | Predeterminado | Uso y límites |
|---|---|---|
| `router_driver` | `mock` | `mock`, `unifi`, `omada`, `opnsense`, `mikrotik`, `virtual_gateway`; elegir según INSTALLATION. |
| `routing_enabled` | `true` | Interruptor global; false retira lo gestionado. Verificar resultado remoto; conserva configuración y destinos. |
| `sync_interval_seconds` | `300` | Segundos entre ciclos; entero ≥ 0. 0 desactiva ciclos automáticos, pero cambios y peticiones manuales siguen reconciliando. |
| `sliding_window_seconds` | `3600` | Retención adicional de IPs DNS. API acepta ≥ 0; panel propone mínimo 60. Se suma al TTL sintético de 60 s, no al TTL autoritativo. |
| `resolve_ipv6` | `false` | Resolver también AAAA. Gateway, MikroTik y Omada solo admiten IPv4: los ajustes y destinos explícitos incompatibles se rechazan. IPv4 seleccionado no cubre conexiones IPv6 del cliente. |
| `feed_isp_filter` | vacío | Filtro de operador de la fuente comunitaria; vacío incluye todos. No modifica su señal global ni afecta destinos manuales. |
| `feed_force_active` | `false` | Ignora señal global comunitaria y utiliza incidencias recientes; mantiene controles de frescura y horario. No activa un feed pausado. |
| `hayahora_feed_url` | Fuente comunitaria integrada | URL HTTP(S) sin credenciales. Cambiarla actualiza el feed que usaba la URL anterior y reinicia su caché HTTP. |
| `feed_max_age_seconds` | `3600` | Solo API: antigüedad máxima comunitaria, mínimo 60 s. Error conserva snapshot válido anterior y degrada estado. |
| `feed_lookback_hours` | `24` | Solo API: ventana de incidencias comunitarias; entero ≥ 1. |
| `feed_max_targets` | `16384` | Solo API: máximo de destinos por feed, entero ≥ 1; el exceso rechaza el documento, no lo trunca. |
| `active_days` | vacío | Días comunitarios separados por coma: `mon,tue,wed,thu,fri,sat,sun`; vacío = todos. |
| `active_hours` | vacío | Una o varias franjas separadas por coma, como `12:00-14:00,18:00-23:00`; vacío = todo el día. Inicio incluido, fin excluido. Una franja nocturna pertenece al día de inicio; inicio y fin iguales se rechazan. |
| `active_timezone` | `UTC` | Zona IANA, por ejemplo `Europe/Madrid`; interpreta días y franja. |
| `notification_url` | vacío | Webhook opcional HTTPS de transiciones; vacío lo desactiva. No admite credenciales en URL. |
| `notification_token` | vacío | Secreto Bearer opcional del webhook; vacío conserva token existente. |
| `gateway_fail_closed` | `true` | Solo gateway virtual. Sin ruta por el túnel, bloquea destinos seleccionados; false permite fallback normal. No configura kill-switch en otros routers. |
| `log_level` | `INFO` | Solo API; persistido y aplicado al logger de app al cargar ajustes en arranque. Reiniciar para asegurar aplicación. |

Horario solo restringe contribuciones comunitarias, no destinos manuales ni feeds genéricos.
Con intervalo 0 no hay evaluación autónoma del horario: ejecutar sincronización manual
para aplicar transiciones. Si se necesita funcionamiento automático, usar intervalo positivo.

Webhook: POST JSON con `service`, `blocked`, `success`, `routing_enabled`. Se envía cuando
cambia esa combinación frente al último envío confirmado; no por cada destino. `blocked`
refleja señal de feeds habilitados, no una prueba de tráfico. Timeout 5 s; fallo reintenta
en el siguiente ciclo, con posible duplicado. No existe cola de cada transición intermedia.

## UniFi — Ajustes → Router

| Clave | Predeterminado | Uso |
|---|---|---|
| `unifi_controller_url` | vacío | URL del controlador/UniFi OS accesible desde la aplicación. API V2 Traffic Routes requerida. |
| `unifi_api_key` | vacío | API key local; secreto. Se envía al controlador, nunca al cliente de red. |
| `unifi_site` | `default` | Identificador del sitio. |
| `unifi_route_name` | `TeByPass` | Nombre de Traffic Route gestionada; mantenerlo estable para evitar conflictos. |
| `unifi_vpn_network_id` | vacío | ID del cliente VPN existente. Guardar acceso y usar recarga de la lista VPN en panel para seleccionar explícitamente. Vacío reutiliza la VPN de la regla existente o la primera descubierta. |
| `unifi_verify_tls` | `false` | Verificación del certificado del controlador. Activarla con certificado confiable; el valor inicial actual no verifica. |
| `unifi_username` | vacío | Solo API, campo heredado: no implementa login usuario/contraseña en este driver. Usar API key. |
| `unifi_password` | vacío | Solo API, secreto heredado sin autenticación efectiva en el driver actual. |
| `unifi_group_name` | `TeByPass_Targets` | Solo API, compatibilidad heredada; el driver V2 actual no utiliza un grupo con este nombre. |

Política actual para todos los clientes alcanzados por la regla. No hay selección
individual de clientes desde el panel. El ejemplo de recorrido está en el README.

## Omada — Ajustes → Router

| Clave | Predeterminado | Uso |
|---|---|---|
| `omada_base_url` | vacío | URL base del controlador con Open API habilitada. |
| `omada_client_id` | vacío | Client ID de la aplicación registrada. |
| `omada_client_secret` | vacío | Secreto de esa aplicación. |
| `omada_controller_id` | vacío | Controller/Omada ID del controlador. |
| `omada_site` | `Default` | Sitio administrado. |
| `omada_target_type` | `vpn` | Tipo de salida: `vpn` o `wan`. |
| `omada_target` | vacío | Nombre o ID de la salida existente del tipo elegido. |
| `omada_route_name` | `TeByPass` | Nombre de la política gestionada. |
| `omada_verify_tls` | `true` | Verificar certificado del controlador. |

## OPNsense — Ajustes → Router

| Clave | Predeterminado | Uso |
|---|---|---|
| `opnsense_host` | vacío | Dirección del firewall accesible por su API. |
| `opnsense_api_key` | vacío | API key; secreto. |
| `opnsense_api_secret` | vacío | API secret; secreto. |
| `opnsense_alias_name` | `TeByPass_Targets` | Alias destino preparado previamente y referenciado por la regla. |
| `opnsense_route_name` | `TeByPass` | Descripción exacta de la regla Rules [new]/Automation Filter previamente creada con gateway VPN. |
| `opnsense_verify_tls` | `true` | Verificar certificado del firewall. |

TeByPass actualiza alias y activa/desactiva la regla; no crea la topología ni el túnel.
El gateway se selecciona en la regla del firewall.

## MikroTik — Ajustes → Router

| Clave | Predeterminado | Uso |
|---|---|---|
| `mikrotik_api_url` | vacío | URL REST RouterOS v7; el driver añade `/rest` si falta. |
| `mikrotik_username` | vacío | Usuario con acceso REST y permisos para las reglas gestionadas. |
| `mikrotik_password` | vacío | Contraseña; secreto. |
| `mikrotik_address_list` | `TeByPass_Targets` | Prefijo de propiedad de reglas, pese al nombre histórico de la clave. Usa comentario `<prefijo>:managed`; no crea una address-list. |
| `mikrotik_routing_table` | vacío | Tabla FIB existente con ruta por defecto a la VPN. Usa lookup-only-in-table; revisar prioridades, FastTrack y mangle. |
| `mikrotik_verify_tls` | `true` | Verificar certificado del router. |

## WireGuard — Ajustes → WireGuard

[Subida paso a paso](../README.md#cargar-la-configuración-wireguard). No es una clave de
settings: utiliza endpoints propios. Máximo 64 KiB, Interface con PrivateKey y al menos
un Peer con PublicKey y AllowedIPs. Directivas admitidas:

- Interface: PrivateKey, Address, ListenPort, MTU, DNS, Table.
- Peer: PublicKey, PresharedKey, AllowedIPs, Endpoint, PersistentKeepalive.

Claves válidas Base64 de 32 bytes. Hooks y directivas desconocidas se rechazan.
Table debe ser off si se proporciona; se añade si falta. DNS se elimina del archivo
aplicado: los clientes mantienen su DNS. AllowedIPs debe cubrir los destinos seleccionados;
AllowedIPs no convierte por sí mismo todos los destinos en seleccionados por TeByPass.
Una configuración de túnel válida no acredita handshake ni navegación de clientes.
Un fallo al reemplazarla solicita restaurar el estado anterior; el error distingue si
la restauración también falló. `wireguard/wg0.last-good.conf` conserva la última aplicada
con permisos 0600. Incluirlo en copias y actualizar aplicación y sidecar juntos.

## Bootstrap y Docker

No son ajustes cambiables por PUT. En una instalación Compose, `.env` sustituye variables
del YAML; no equivale a inyectar automáticamente todas las variables dentro del contenedor.
Guardar estos parámetros solo en el host; recrear servicios cuando cambien.

| Variable Compose | Uso / valor base |
|---|---|
| `TEBYPASS_PORT` | Puerto publicado, 9384. Internamente la aplicación escucha siempre en 9384. |
| `TEBYPASS_DATABASE_URL` | SQLite asíncrono, `sqlite+aiosqlite:////data/tebypass.db`; mantener dentro del volumen persistente. |
| `TEBYPASS_IMAGE_PREFIX` | Base de nombres de imagen, `ghcr.io/tscneo/tebypass`; sidecar añade `-gateway`. |
| `TEBYPASS_IMAGE_TAG` | Etiqueta común, base `latest`; usar versión publicada existente. `edge` sigue main. El override build usa imágenes locales. |
| `TEBYPASS_TRUSTED_PROXIES` | Metadatos reenviados; Compose la pasa como FORWARDED_ALLOW_IPS. No necesaria para login web. Ver OPERATIONS. |
| `TEBYPASS_LAN_NETWORK` | Nombre de macvlan externa, `tebypass-lan`; solo gateway. Debe crearse previamente. |
| `TEBYPASS_GATEWAY_IP` | IP LAN libre del sidecar; el valor del YAML es un ejemplo reservado, no una autodetección. |
| `TEBYPASS_GATEWAY_MAC` | MAC local fija sin colisiones, vinculada a reserva/exclusión DHCP. |
| `TEBYPASS_GATEWAY_SET` | Nombre nft del sidecar, `tebypass_targets`; opción avanzada, mantener coherente con el driver. |
| `TEBYPASS_GIT_COMMIT` | Solo build override: commit embebido, `unknown` si no se proporciona. |
| `TEBYPASS_LAN_PARENT`, `TEBYPASS_LAN_SUBNET`, `TEBYPASS_LAN_GATEWAY` | Auxiliares para crear la red; figuran en comentarios, no configuran por sí solas la red Docker externa. |

Parámetros internos/ejecución directa Python, fuera del recorrido de instalación habitual:
`host`, `port`, `database_url`, `db_path` seleccionan escucha y BD; `secret_key` y
`master_pin_hash` no son opciones de administración desde settings. La autenticación
actual usa admin_token, no un PIN de usuario. `gateway_nft_set_name`,
`gateway_control_socket`, `wireguard_config_path` fijan integración local (por defecto
tebypass_targets, socket de datos y `/data/wireguard/wg0.conf`).
El driver admite `TEBYPASS_GATEWAY_SOCKET` y la clave de cifrado puede inyectarse mediante
`TEBYPASS_SECRET_KEY`; no rotarla sin migrar credenciales. El Compose fija el socket
compartido `/data/gateway.sock`. No personalizar una sola mitad de esta integración.
Las opciones de mantenimiento del entrypoint del sidecar `TEBYPASS_CONTROLLER_BIN` y
`TEBYPASS_WG_DIR` cambian ejecutable/directorio internos; `TEBYPASS_GATEWAY_WG_IFACE`
selecciona interfaz (wg0) y `TEBYPASS_GATEWAY_CHECKPOINT` el checkpoint de destinos
(`/data/gateway_targets.json`). No sustituyen la configuración
operativa. Mantener sus valores del contenedor salvo desarrollo.

Mock no requiere credenciales y no desvía tráfico. Ningún driver crea una VPN comercial,
suministra claves o garantiza compatibilidad de todos los firmwares de una marca.
