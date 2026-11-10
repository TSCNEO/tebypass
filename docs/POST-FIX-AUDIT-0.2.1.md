# Auditoría posterior a las correcciones — 0.2.1

Fecha: 2026-11-10. Se repitieron las seis reproducciones de la auditoría inicial después
de corregirlas y se ampliaron los casos adversos. No se cambiaron redes de producción.

## Resultado local

145 pruebas correctas, sin xfail ni skip. Ruff, formato y Mypy correctos.
Las antiguas pruebas de fallo son ahora regresiones obligatorias.

- F01: el mismo FQDN conserva identidad y resoluciones tras refresco sin cambios;
  fallo DNS no retira la IP aún vigente. Las entradas eliminadas siguen retirándose.
- F02/F06: crear/editar/importar IPv6 con gateway, MikroTik u Omada se rechaza. Feed
  mixto incompatible conserva el snapshot y señal anteriores. Cambio de driver con
  CIDR o resoluciones IPv6 vigentes se rechaza antes de teardown.
- F03: fallo al levantar WireGuard o instalar ruta restaura archivo, túnel y ruta
  previos en la orquestación simulada. Se comprueba fallo de rollback, permisos 0600,
  persistencia de última configuración aplicada tras recreación y borrado explícito.
- F04: actualizar regla UniFi restringida converge a ALL_CLIENTS y exige comprobarlo.
- F05: ACK ausente, incompleto o de política distinta impide sincronización. La
  observación comprueba marca, prioridad, máscara, tabla y ruta de protección.
- Recorrido completo de control: arranque, fuente inicial pausada, destino manual,
  sincronización, persistencia/reinicio y purga comprobados con lifespan, HTTP y SQLite.

## Validación de paquetes y distribución

El script Linux de política se amplió con un reemplazo de WireGuard que falla al
instalar la ruta; exige restauración del túnel anterior y ruta efectiva en kernel.
El pipeline de publicación ejecuta ese script, además de reenvío DNS/HTTP desde cliente,
transferencias, caída del túnel y recuperación, antes de promover imágenes.

La etapa checks de [CI del código corregido](https://github.com/TSCNEO/tebypass/actions/runs/42797085007)
ha pasado: dependencias fijadas, 145 pruebas y ambos scripts de red Linux. Las imágenes
app/gateway se han construido para amd64/arm64. La instalación descargada y promoción
se verifican en las siguientes etapas del mismo flujo antes de publicar la release.
La disponibilidad local de Docker no sustituye esos resultados; los mocks no acreditan
reenvío real. La validación de hardware de routers y de cada LAN permanece fuera de esta
prueba automatizada. Arm64 se compila; las pruebas de ejecución del pipeline son amd64.

## Límites conservados

Una política/driver/worker; gateway en el mismo host por socket/volumen. Gateway,
MikroTik y Omada son IPv4. No se crean VPNs de router ni se inspeccionan aplicaciones.
La presencia de interfaz o handshake no prueba accesibilidad de todos los destinos.
Una IP compartida puede afectar otros servicios alojados allí; DNS de cliente y servidor
pueden diferir. Tras un 502 de WireGuard, comprobar qué túnel está aplicado antes de
reintentar. La candidata guardada puede diferir de la última aplicada.

Los seis hallazgos originales tienen regresiones que pasan; esto no equivale a certificar
que no exista ningún defecto ni que todos los firmwares se comporten igual.
