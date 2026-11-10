# TeByPass 0.2.1

Primera versión estable publicada: enrutamiento selectivo por destino con panel en
español, API autenticada y configuración persistente. Gateway virtual opcional: con
router compatible se ejecuta solo la aplicación.

Correcciones de la auditoría funcional:

- Las fuentes conservan identidad de destinos y retención DNS al refrescarse.
- Ingesta, importación y cambios de driver rechazan IPv6 incompatible antes de modificar
  políticas. Gateway, MikroTik y Omada son IPv4.
- UniFi reconcilia y verifica ALL_CLIENTS, además de destinos, habilitación y salida.
- El gateway exige confirmación de política y verifica regla de marca y ruta fail-closed.
- Un reemplazo fallido de WireGuard intenta restaurar configuración, túnel y ruta previos.
  Informa por separado cuando la restauración también falla. La última configuración
  aplicada se conserva en el volumen para recuperación tras recrear el sidecar.

Incluye guía de instalación manual, protocolo de entrevista para LLM, referencia de
opciones y API, y ejemplos de router compatible y gateway virtual.

## Instalación y actualización

Descargar `docker-compose.yml` y `SHA256SUMS` de esta release, verificar el checksum del
Compose y ejecutar `docker compose up -d --wait tebypass`. El gateway requiere preparación
macvlan y el perfil opcional. Consultar las guías de la versión antes de habilitarlo.

Actualizar **ambas imágenes a 0.2.1** si se utiliza gateway virtual: aplicación y sidecar
comparten contrato de verificación de política. Guardar copia consistente del volumen,
BD, claves y directorio WireGuard antes de actualizar. No eliminar volúmenes.

Una política de salida y un worker por instalación. No hay sidecar remoto ni varias VPN
por carpeta. Las pruebas de laboratorio no certifican todos los modelos/firmwares ni
sustituyen comprobar el tráfico desde los clientes de cada instalación.

Gracias a [jcastro/stopliga](https://github.com/jcastro/stopliga) y a la comunidad
[hayahora.futbol](https://hayahora.futbol). Licencia AGPL-3.0-or-later.
