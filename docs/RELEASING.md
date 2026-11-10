# Publicación de imágenes y primera release

La entrega **0.2.1** corrige los seis casos de aceptación de la auditoría funcional,
con alcance y límites indicados en README/PROJECT. No se anuncia certificación de todos los routers ni compatibilidad IPv6
del gateway. Este procedimiento no convierte automáticamente un repositorio privado en público.

## Antecedente de publicación (2026-11-09)

Candidata `v0.2.0-rc.3` preparada en borrador pre-release. Main y candidata pasaron el
flujo completo, incluidas imágenes descargadas de GHCR, persistencia, pruebas de kernel
y checksums de assets descargados. Evidencia en [PHASES.md](PHASES.md#distribución-de-imágenes-y-candidata-de-primera-release).
El repositorio ya es público. La candidata continúa en borrador y no hay release pública.
Antes de anunciar instalación por release, comprobar visibilidad y pull anónimo de ambos
paquetes y publicar la entrega elegida. No presuponer disponibilidad de `latest`.

El estado de verificación de 0.2.1 se registra en la
[auditoría posterior](POST-FIX-AUDIT-0.2.1.md).

## Qué hace GitHub

`Container images` se ejecuta al cambiar main, recibir un tag `v*` o solicitar ejecución
manual sobre main/tag. Antes de publicar invoca la CI reutilizable: Ruff, formato, Mypy,
Pytest y pruebas Linux de nftables, WireGuard y reenvío de clientes.

Construye `ghcr.io/tscneo/tebypass` y `ghcr.io/tscneo/tebypass-gateway` para Linux amd64 y
arm64, con SHA del código, versión, licencia, SBOM y procedencia de build. Usa GITHUB_TOKEN
con permiso packages:write solo en jobs de publicación; no requiere PAT en secrets.

Primero publica `build-<run>-<attempt>`, único por ejecución para evitar colisiones
entre main y tags que compartan commit. Un runner descarga esas imágenes e instala el
Compose sin build: comprueba liveness, autenticación, guardado, rechazo de origen ajeno,
versión embebida y pruebas reales del gateway. Solo después promueve canales/versiones:

| Referencia | Etiquetas |
|---|---|
| main | edge, sha de 12 caracteres |
| v0.2.0-rc.3 | 0.2.0-rc.3; no latest |
| v0.2.0 | 0.2.0, latest |

La versión base del tag debe coincidir con pyproject.toml. No reutilices tags de release
ni versiones publicadas: una corrección debe tener otro número. No se despliega en tu
servidor desde Actions. Las pruebas de ejecución de CI son amd64; arm64 se compila y
se publica, sin afirmar validación de hardware arm64.

Con tag, genera un artefacto de instalación y crea un **borrador de GitHub Release** con
Compose fijado a esa versión, env.example opcional, INSTALL.md, licencias y SHA256SUMS.
Descarga de nuevo los assets publicados y verifica sus checksums antes de terminar.
Si la release ya existe, adjunta/actualiza esos assets. Main no crea releases.

## Antes de abrir la primera release

1. Revisar licencia, código y todo el historial que se vaya a hacer público. Las normas
   actuales de ejemplos no acreditan por sí solas la ausencia de datos antiguos.
2. Confirmar CI y `verify-install` de la versión candidata. Revisar límites conocidos,
   notas de release y procedimiento de actualización/rollback.
3. Publicar el código fuente y las imágenes para que la instalación no requiera login.
   El repositorio ya es público; comprobar por separado los paquetes. GHCR crea paquetes privados por
   defecto: tras su primera publicación, en cada paquete → Package settings → Change
   visibility, establecer Public. No confundirlo con visibilidad del repositorio.
4. Comprobar pull anónimo de **ambas** imágenes desde un Docker limpio, instalar usando
   solo el Compose descargado y verificar login, guardado y persistencia tras recreación.
5. Crear/push del tag cuando se autorice la versión, comprobar el workflow y revisar el
   borrador con assets antes de pulsar Publish release. Una release preliminar debe
   marcarse Pre-release. El tag ya publica sus imágenes antes de publicar el borrador.

Ejemplo del mantenedor para una candidata, no ejecutado como parte de esta preparación:

```sh
git tag -a v0.2.0-rc.3 -m 'TeByPass 0.2.0 release candidate 1'
git push origin v0.2.0-rc.3
```

GitHub limita el cambio inicial de visibilidad de paquetes a sus ajustes; el flujo no
intenta eludir esa configuración. Referencias oficiales:
[GHCR y visibilidad](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[build multiplataforma](https://docs.docker.com/build/ci/github-actions/multi-platform/).
