# Reglas de documentación

- README y docs/: uso, instalación, configuración, API y límites vigentes.
- maintenance/: desarrollo, arquitectura, decisiones aceptadas y publicación.
- plan/: propuestas pendientes/en curso, con estado y criterios de aceptación.
- plan/done/: histórico cerrado, solo por consulta histórica expresamente solicitada.

Un LLM de despliegue usa README y docs/LLM_DEPLOYMENT. No debe explorar plan, done ni
maintenance; los documentos históricos nunca completan una supuesta configuración faltante.

## Comprobar cobertura al cambiar funciones

- Campo de Settings/settings_store: documentar clave, valor inicial, alcance, validación
  y si aparece en panel, solo API, es interno o heredado, en CONFIGURATION.
- Endpoint: actualizar API y ejemplos contra OpenAPI, incluyendo autenticación y errores.
- Botón/acción: revisar JavaScript y describir sus efectos reales en USER_GUIDE; diferenciar
  guardar, probar y aplicar, especialmente si una acción tiene efectos remotos.
- Docker/instalación: mantener Compose, env.example y guías coherentes. Validar distribución
  y checksums con las herramientas de publicación.
- Arquitectura/contrato: actualizar ARCHITECTURE y DECISIONS de maintenance. Trasladar los
  límites que afecten a usuarios a docs/SCOPE y a la guía correspondiente.

Verificar enlaces y anclas tras mover archivos, además de referencias en scripts, pruebas,
CI y empaquetado. Un informe fechado no sustituye mantener una guía vigente.

## Cierre de planes e informes

Al completar aceptación, actualizar primero las guías vigentes y retirar el plan del índice
activo. Eliminar borradores redundantes o mover el documento a done con estado, fecha,
resultado y commit/release. Archivar también informes de descubrimiento ya resueltos.
No dejar roadmaps paralelos ni planes completados mezclados con instrucciones de usuarios.

Si no se terminó el trabajo, conservarlo pendiente con alcance real y pruebas que faltan;
no archivar como hecho para limpiar carpetas. El histórico se conserva para explicar cuándo
se decidió algo, pero no se consulta como fuente de instrucciones o tareas actuales.

El archivo .ignore excluye plan/done de búsquedas rg por defecto. Solo anular esa
exclusión para una consulta histórica explícita; no hacerlo para recuperar instrucciones.
