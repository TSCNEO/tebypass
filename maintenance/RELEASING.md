# Publicar una versión

Procedimiento vigente del mantenedor. Para instalar una entrega publicada, usar
[la guía de instalación](../docs/INSTALLATION.md).

## Preparar

1. Completar aceptación del cambio y comprobaciones de CONTRIBUTING. Retirar planes
   terminados del índice activo; actualizar guías de uso y límites implementados.
2. Elegir una versión nueva, sin reutilizar tags publicados. Mantener pyproject.toml,
   uv.lock, metadatos de versión y valor de build coherentes. Preparar docs/RELEASE_NOTES.md.
3. Revisar los cambios que se publican, licencia y ejemplos de documentación sin datos
   de instalaciones. Confirmar visibilidad pública de código y ambos paquetes GHCR.
4. Integrar código verificado en main y comprobar su push con git ls-remote.
5. Crear y subir tag vX.Y.Z (estable) o vX.Y.Z-rc.N/alpha.N/beta.N. La base del tag debe
   coincidir con pyproject.toml. El tag autoriza publicar imágenes; la release queda en borrador.

## Pipeline

Container images ejecuta la CI reutilizable con dependencias fijadas: Ruff, formato,
Mypy, Pytest, pruebas kernel nftables/WireGuard y reenvío de clientes con DNS/HTTP,
caída del túnel y recuperación. Los cambios solo documentales no necesitan construir imágenes.

Construye app y gateway para Linux amd64/arm64, con commit, versión, licencia, SBOM y
procedencia. La ejecución de pruebas de contenedores es amd64; no equivale a validar
hardware arm64 ni todos los firmwares de router.

Publica primero build-<run>-<attempt>. verify-install descarga esas imágenes, comprueba
instalación, autenticación, persistencia, versión y pruebas de red; después se promueven:

- main: edge y sha-<12 caracteres>.
- Tag preliminar: versión del tag, sin latest estable.
- Tag estable: versión del tag y latest.

GitHub usa GITHUB_TOKEN con packages:write para publicar; no necesita PAT en secrets.
No se despliega en servidores de usuarios desde Actions.

Con tag, scripts/prepare_release.py genera Compose fijado a versión, env.example,
INSTALL.md, licencias y SHA256SUMS. El flujo adjunta assets al borrador y los descarga
para verificar checksums. No publicar el borrador hasta terminar todas las comprobaciones.

## Verificar y publicar

1. Confirmar workflow correcto para el SHA exacto del tag y metadatos de ambas imágenes.
2. Comprobar descarga de imágenes sin credenciales: ser público el repositorio no hace
   públicos los paquetes. Si hace falta, abrir cada paquete en GitHub → Package settings
   → Change visibility → Public. La clave SSH de Git no permite cambiar esa visibilidad.
3. Verificar instalación usando únicamente Compose publicado, login, guardado, persistencia
   y salud; con gateway, comprobar también contrato y pruebas de tráfico del sidecar.
4. Descargar assets, verificar SHA256SUMS y comprobar que Compose fija ambas imágenes.
5. Publicar el borrador como estable o pre-release según el tag. Para estable, comprobar
   latest y descarga anónima de todos los assets después de publicar.
6. Registrar resultado, versión y evidencia sanitizada del cierre, sin convertir el informe
   en documentación operativa. Mantener las guías vigentes y archivar el informe cerrado.

La comprobación autenticada de CI no acredita descarga pública. Actualizar aplicación
y sidecar juntos cuando cambie su contrato. Actualización y rollback para usuarios están
descritos en [Operación](../docs/OPERATIONS.md#actualizar-y-revertir).
