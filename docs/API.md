# API de administración

Base: `/api/v1`. Contrato ejecutable y esquemas completos en `/openapi.json`, `/docs`
y `/redoc`, todos autenticados. Las tablas siguientes cubren las rutas declaradas por
la aplicación, sin contar assets estáticos ni páginas automáticas de FastAPI.

## Acceso

Para automatización usar `Authorization: Bearer <CLAVE_ADMIN>` con la clave local
`/data/admin_token`. Obtenerla por un mecanismo privado; no pegarla en chat ni guardarla
en scripts versionados. El middleware exige autenticación aunque el esquema OpenAPI
no presente un botón Bearer dedicado: un cliente HTTP puede añadir la cabecera directamente.

El navegador inicia sesión mediante `/auth/session`: JSON con `token` y `browser_origin`
(URL de origen, sin ruta), junto con cabecera Origin y Host coincidentes. Cookie HttpOnly,
SameSite strict, duración 12 horas y Secure para acceso HTTPS. Escrituras con sesión
moderna exigen Origin igual al firmado. Para agentes, Bearer evita reproducir el login web.

Públicos: GET `/healthz`, GET `/login`, POST `/auth/session` (valida credencial), assets.
DELETE `/auth/session` también pasa por la excepción de middleware y elimina la cookie.
GET `/` redirige a login si no está autenticado.

## Rutas y operaciones

| Método | Ruta | Operación / cuerpo |
|---|---|---|
| GET | `/` | Panel HTML. |
| GET | `/login` | Formulario de acceso. |
| POST | `/auth/session` | Crear sesión: `token`, `browser_origin` opcional; usar flujo de origen descrito arriba. |
| DELETE | `/auth/session` | Cerrar sesión del navegador; no rota clave administrativa. |
| GET | `/healthz` | Liveness público y esquema HTTP observado; no prueba router/VPN. |
| GET | `/api/v1/status` | Estado global, driver, contadores, errores de feeds y última sincronización. |
| GET | `/api/v1/version` | Versión y metadatos de build. |
| GET | `/api/v1/settings` | Ajustes editables con secretos enmascarados. |
| PUT | `/api/v1/settings` | Objeto parcial de claves de CONFIGURATION; guarda y solicita reconciliación. |
| GET | `/api/v1/settings/unifi/vpn-networks` | Clientes VPN descubiertos con credenciales UniFi guardadas. |
| POST | `/api/v1/settings/test-router` | Sonda del driver guardado; `healthy`, `driver`, `details`. |
| POST | `/api/v1/settings/router/purge` | Retirar política gestionada y guardar pausa global; verificar éxito. |
| GET | `/api/v1/targets` | Destinos con lista, origen feed e IPs vigentes. |
| POST | `/api/v1/targets` | Crear: `value`, opcionales `destination_type`, `description`, `enabled`, `folder_id`. |
| PATCH | `/api/v1/targets/{target_id}` | Cambios parciales: value, description, enabled, folder_id. `folder_id: null` desasigna. |
| DELETE | `/api/v1/targets/{target_id}` | Borrar destino y solicitar reconciliación. |
| POST | `/api/v1/targets/bulk` | `entries` array de cadenas/líneas, `folder_id` y `description` opcionales; created/skipped/invalid. |
| POST | `/api/v1/targets/{target_id}/resolve` | Resolver FQDN y persistir IPs; CIDR devuelve el propio prefijo. |
| GET | `/api/v1/routes/active` | Prefijos observados por el driver; puede fallar si no es posible consultar el router. |
| POST | `/api/v1/sync` | Cuerpo opcional `{"force": false}`. Force solicita refresco de fuentes sin ETag; no significa activar modo comunitario forzado. |
| GET | `/api/v1/folders` | Listas y número de destinos. |
| POST | `/api/v1/folders` | `name` (1–128 caracteres), `color` opcional; nombre único sin distinguir mayúsculas. |
| PATCH | `/api/v1/folders/{folder_id}` | Renombrar/recolorear con name/color. |
| DELETE | `/api/v1/folders/{folder_id}` | Borrar lista conservando sus destinos sin asignación. |
| GET | `/api/v1/feeds` | Fuentes, señal y fechas/errores. |
| POST | `/api/v1/feeds` | Crear: `name`, `url`, `format` (json por defecto), `enabled` (true por defecto). |
| DELETE | `/api/v1/feeds/{feed_id}` | Borrar fuente y sus destinos, reconciliar. |
| POST | `/api/v1/feeds/{feed_id}/toggle` | Invertir habilitación; no es idempotente. Leer estado antes de reintentar. |
| POST | `/api/v1/feeds/{feed_id}/fetch` | Descarga forzada; `imported: 0` puede ser 304, vacío o error; consultar last_error. |
| POST | `/api/v1/feeds/{feed_id}/clear` | Vaciar destinos, pausar fuente y limpiar caché HTTP. |
| GET | `/api/v1/gateway/wireguard` | Configurado, peers, fecha y vista parcial con claves ocultas. |
| POST | `/api/v1/gateway/wireguard` | `content` con texto completo; configured/applied son estados distintos. |
| DELETE | `/api/v1/gateway/wireguard` | Borrar archivo y solicitar detener túnel si driver virtual seleccionado. |

No existe endpoint de actualización genérica de feeds ni de exportación/backup de BD.
No hay endpoints de PIN: los campos/modelos heredados no sustituyen la autenticación
administrativa. `pin` en la petición de sync no aporta una segunda autorización.
La opción force no garantiza invalidar toda la retención DNS; las IPs conservadas
siguen su ventana. Para refrescar un FQDN usar también su operación resolve.

## Ejemplos de cuerpos

Los siguientes son cuerpos JSON para enviar con un cliente autenticado; nombres y redes
son de documentación. No incluir secretos en ejemplos compartidos.

Crear un destino manual pausado, sin tocar tráfico durante la preparación:

```json
{"value": "198.51.100.80/32", "enabled": false, "description": "Prueba de formato"}
```

Registrar fuente genérica pausada:

```json
{"name": "Destinos de ejemplo", "url": "https://feed.example.net/targets.json", "format": "json", "enabled": false}
```

Ajustar frecuencia sin reenviar toda la configuración ni sobrescribir secretos:

```json
{"sync_interval_seconds": 300}
```

Consultar respuesta y después `/api/v1/status`: HTTP 200 al guardar no acredita aplicación
remota. Tras un timeout, leer estado antes de reintentar creaciones o toggle para evitar
duplicados o invertir por segunda vez una opción.

## Errores y estado

- 400/422: validación; revisar detalle y contrato del endpoint.
- 401: falta credencial o es inválida; iniciar sesión/usar Bearer válido.
- 403: origen no autorizado de sesión; revisar URL/Host e iniciar sesión de nuevo.
- 404: recurso inexistente. 409: conflicto, como nombre duplicado de lista.
- 502: fallo de comunicación o retirada/aplicación remota. WireGuard puede estar guardado
  aunque no se haya aplicado. Restablecer dependencia y reintentar conscientemente.
- Sync puede responder HTTP 200 con `success: false`. `total_active: -1` es observación
  desconocida. No tratarlo como rutas vacías ni como éxito.

Endpoints de borrado de targets/feeds/folders devuelven 204 sin cuerpo. Otras operaciones
usan respuestas JSON; consultar OpenAPI para campos y tipos exactos.
