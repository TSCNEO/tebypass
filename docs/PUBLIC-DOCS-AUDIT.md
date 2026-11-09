# Revisión de documentación pública

Fecha: 2026-11-09. Alcance: documentación de incorporación y despliegue, contrastada
con Compose, drivers, contratos y pruebas del checkout. No constituye una auditoría
integral de seguridad ni una certificación de equipos o de ausencia de secretos históricos.

## Hallazgos corregidos

- README mezclaba presentación, detalles internos y referencias al entorno de validación.
  Ahora presenta el producto, componentes y acceso a guías separadas.
- El arranque propuesto dependía de una release pública inexistente. GitHub confirmó
  repositorio público y candidata `v0.2.0-rc.3` en borrador. La guía ofrece compilación
  y condiciona la descarga de release a su disponibilidad, sin afirmar pull anónimo GHCR.
- La guía asistida empezaba por acceso/proxy sin elegir arquitectura. Ahora entrevista
  por etapas y evita el sidecar con drivers de router compatibles.
- AGENTS anunciaba sidecar remoto, incompatible con el socket y volumen compartidos
  actuales. Se corrigió y se condicionó el protocolo macvlan a elegir gateway virtual.
- La auditoría inicial podía leerse como dictamen vigente. Se identifica como histórica.
- Instrucciones de desarrollo separadas del recorrido de instalación en CONTRIBUTING.

## Verificación

- `pytest --basetemp=/tmp/tebypass-public-docs-tests`: 120 passed.
- `ruff check .`, `ruff format --check .`, `mypy app tests`: correctos.
- Revisión de enlaces locales y `git diff --check`.
- Cambios sin credenciales ni inventario de instalaciones; ejemplos de red reservados
  para documentación. No se han modificado runtime, contratos ni políticas de routers.

## Comprobaciones externas pendientes

La publicación de una release y visibilidad anónima de sus dos paquetes se verifican
antes de anunciar instalación binaria. La conectividad real debe comprobarse en cada
instalación desde un cliente; mocks y salud HTTP no la acreditan. El historial Git y
los artefactos publicados requieren una revisión específica si se busca certificar
que nunca contuvieron datos sensibles; esta revisión documental no aporta esa garantía.
