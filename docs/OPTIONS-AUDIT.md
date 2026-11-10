# Cobertura de documentación de opciones

Revisión: 2026-11-10. Fuente de verdad: código del checkout, no una lista de funciones
previas. Se revisaron Settings/settings_store, plantilla y acciones JavaScript,
OpenAPI y adaptadores HTTP, servicios, Compose y controlador del gateway.

## Inventario contrastado

| Superficie | Cobertura | Referencia |
|---|---|---|
| Configuración | 57 campos de Settings, 48 editables | CONFIGURATION: cada clave, valor inicial, alcance y opciones heredadas |
| Panel | 32 bindings directos de settings y campos dinámicos de Omada/OPNsense | CONFIGURATION y USER_GUIDE |
| API | 31 operaciones OpenAPI; además panel y login HTML | API: método, ruta, propósito y cuerpo |
| Docker | 14 variables presentes en Compose/override/env.example | CONFIGURATION: despliegue y distinción entre variables activas y auxiliares |
| Acciones | Destinos, listas, importación, resolución, feeds, sincronización, pausa/purga, WireGuard | USER_GUIDE, API y ejemplos del README |
| Ciclo de vida | Acceso, proxy, secretos, copia, actualización, reversión y retirada | OPERATIONS y USER_GUIDE |

La verificación mecánica comprobó que cada campo Settings y cada variable Compose aparece
en CONFIGURATION, y que cada operación OpenAPI tiene una fila método/ruta en API.
La lectura manual contrastó efectos y límites: presencia de un nombre no acredita que
su explicación sea correcta. Casos relevantes comprobados:

- Probar conexión en el panel guarda antes de sondear y sincroniza si está saludable;
  no es una consulta sin efectos. Su endpoint aislado usa ajustes ya persistidos.
- El panel no muestra el detalle de entradas inválidas de importación; lo devuelve API.
- Crear/renombrar/borrar listas y crear/eliminar feeds tienen superficies distintas:
  el panel no expone todas las operaciones de la API.
- UniFi username/password/group_name son campos heredados sin función equivalente en
  el driver V2 actual. MikroTik address_list es un prefijo de propiedad de reglas.
- Vaciar secretos no los elimina. Guardar ajustes no acredita aplicación remota.
- Sincronizar forzado no significa activar el modo comunitario forzado.
- Horario solo comunitario, franjas nocturnas y limitación de intervalo cero.
- Fail-closed solo gateway; su salud y una configuración cargada no prueban tráfico.

## Validación y límites

120 pruebas pasadas, Ruff, formato y Mypy correctos. Enlaces locales y secciones de
las guías nuevas/modificadas comprobados, además de diff sin errores de espacios.
Cambios documentales con ejemplos reservados, sin credenciales ni inventario privado.

No se han desplegado routers ni modificado redes. La compatibilidad de firmware,
conectividad del cliente, distribución de imágenes y seguridad del historial público
requieren las comprobaciones específicas de sus guías. Esta revisión no los certifica.

## Mantener la cobertura

Al añadir/quitar un campo en Settings/settings_store, actualizar CONFIGURATION, indicando
si aparece en el panel, solo API, es interno o heredado y qué efectos tiene.
Al cambiar un endpoint, actualizar API y sus ejemplos; al cambiar un botón, revisar
su JavaScript y USER_GUIDE. Revisar Compose y env.example juntos. Después comprobar
los recorridos de primera instalación, configuración, prueba, pausa, actualización y
retirada. No trasladar toda la referencia al README.
