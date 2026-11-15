# TeByPass 0.2.2

Segunda publicación estable orientada a pruebas abiertas de usuarios, sobre la base de 0.2.1, con correcciones integrales de diagnóstico, seguridad operativa y usabilidad en panel y drivers:

- **UniFi Traffic Routes V2:** gestión segura de estados deshabilitados (`is_active=false`), lectura estricta de redes VPN (sin degradar errores HTTP a lista vacía), selección explícita obligatoria cuando existen varias VPNs y detección previa con credenciales candidatas (`POST /api/v1/settings/unifi/vpn-networks`).
- **Sondas sin efectos secundarios y errores saneados:** **Probar conexión** evalúa los valores candidatos del formulario sin guardarlos ni sincronizar rutas; **Guardar ajustes** ejecuta una sola sincronización; `/api/v1/routes/active` devuelve `502` saneado ante fallos de observación sin exponer credenciales/URLs.
- **Diagnóstico de drivers:** OPNsense verifica acción, dirección, alias y gateway de la regla y señala verificaciones parciales; MikroTik valida la existencia de la tabla FIB configurada; `virtual_gateway` distingue fallo de socket, ausencia de WireGuard (`pending_wireguard`), interfaz caída y política incompleta.
- **Telemetría DNS y destinos:** errores DNS por objetivo (`NXDOMAIN`, timeout, presupuesto agotado) visibles en la tabla y en `/api/v1/status` (`uncovered_targets`, `dns_errors`, degradación cuando `routing_enabled=true`); unicidad global de destinos manuales entre carpetas (`HTTP 409`); preservación de líneas rechazadas en importación masiva; avisos cuando un feed activo sigue cubriendo un destino manual pausado o borrado.
- **Feeds y Gateway:** filtro de operador inválido (`feed_isp_filter`) informa los operadores válidos y conserva el último snapshot/targets; el sidecar aplica de forma estricta la interfaz `wg0` y falla rápido ante overrides incompatibles; documentación deduplicada y procedimiento verificado de copia y restauración en [OPERATIONS.md](OPERATIONS.md#copia-de-seguridad-y-restauración-sqlite--secretos--wireguard).

## Instalación y actualización

Descargar `docker-compose.yml` y `SHA256SUMS` de esta release, verificar el checksum del
Compose y ejecutar `docker compose up -d --wait tebypass`. El gateway requiere preparación
macvlan y el perfil opcional (`--profile gateway`). Consultar las guías de la versión antes
de habilitarlo.

Actualizar **ambas imágenes a 0.2.2** si se utiliza gateway virtual: aplicación y sidecar
comparten contrato de verificación de política. Guardar copia consistente del volumen,
BD, claves y directorio WireGuard antes de actualizar. No eliminar volúmenes.

Una política de salida y un worker por instalación. No hay sidecar remoto ni varias VPN
por carpeta. Las pruebas de laboratorio no certifican todos los modelos/firmwares ni
sustituyen comprobar el tráfico desde los clientes de cada instalación.

Gracias a [jcastro/stopliga](https://github.com/jcastro/stopliga) y a la comunidad
[hayahora.futbol](https://hayahora.futbol). Licencia AGPL-3.0-or-later.
