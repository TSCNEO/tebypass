# Protocolo de despliegue asistido por LLM

## Fuentes permitidas para desplegar

Usar README.md y exclusivamente las guías vigentes de docs/: INSTALLATION, USER_GUIDE,
CONFIGURATION, API, OPERATIONS y SCOPE; RELEASE_NOTES para actualizar una versión.
Comprobar siempre la versión instalada y su contrato autenticado si hay diferencias.

No leer, buscar ni utilizar plan/ (incluido plan/done), maintenance/ ni informes o planes
recuperados del historial Git para desplegar. Tampoco seguir enlaces desde un plan hacia
un documento antiguo como si fuera vigente. Esos archivos no son requisitos ni prueban
que una función esté implementada. Si falta información operativa, inspeccionar la
versión y preguntar por el dato del entorno: no completar con diseños o auditorías pasados.
Una solicitud explícita de historia se trata por separado y no altera el despliegue.

## Objetivo y reglas

Guiar a una persona sin conocimiento previo del proyecto hasta una instalación mínima
comprobada. Leer primero README, INSTALLATION y la sección operativa del driver elegido.
Completar la entrevista con [USER_GUIDE](USER_GUIDE.md), [CONFIGURATION](CONFIGURATION.md)
y [API](API.md). Los contratos actuales están en el código y en `/openapi.json` autenticado.
Antes de dar por terminado, repasar opciones con el usuario: destinos manuales/importación,
fuentes y filtro, intervalo/retención, IPv6 según driver, horario/zona, notificaciones,
pausa/purga, acceso/copia/actualización. Mantener valores iniciales cuando sean apropiados;
no habilitar funciones opcionales sin una necesidad. Explicar qué se configuró y qué queda
con su valor predeterminado, sin abrumar con campos de otros drivers.

No asumir fabricante, topología, servidor, VPN ni acceso administrativo. Preguntar por
etapas, con pocas preguntas claras, esperar las respuestas que condicionan la siguiente
acción y reutilizar la información ya proporcionada. Explicar para qué se necesita cada
dato. Si no hay acceso a herramientas, dar instrucciones y solicitar resultados sin secretos;
no afirmar que se ejecutó o comprobó algo.

Conservar volúmenes, políticas y configuración existentes. Inspeccionar antes de escribir.
No activar fuentes ni cambiar rutas de clientes hasta elegir y preparar la salida.
No pedir contraseñas, tokens o claves WireGuard en el chat: usar el panel o un mecanismo
seguro local. No imprimir secretos al automatizar API; capturarlos en memoria y usar Bearer.
No guardar inventario, direcciones del entorno ni secretos en Git o informes públicos.

## Etapa 1 — Entender la instalación

Primer mensaje sugerido:

> Para elegir los componentes: ¿qué router/gateway y controlador utilizas (modelo y
> versión si los conoces)? ¿Dónde quieres ejecutar TeByPass y ya tienes Docker Compose?
> ¿Tienes una VPN/salida alternativa configurada y qué dispositivos quieres que la usen?

Determinar también si existe una instalación de TeByPass que deba conservarse.
Inspeccionar su Compose, servicios y versión sin volcar `.env` ni credenciales.
Si el usuario no conoce un dato, explicar cómo consultarlo de forma no destructiva.
No solicitar detalles de macvlan o DHCP antes de saber que necesita gateway virtual.

## Etapa 2 — Elegir el driver con el usuario

| Respuesta | Siguiente paso |
|---|---|
| UniFi con Traffic Routes V2 y VPN | Preparar API key y seleccionar VPN; solo aplicación |
| Omada con Open API y WAN/VPN | Preparar IDs, sitio y credenciales; solo aplicación |
| OPNsense | Preparar alias/regla Automation Filter y gateway; solo aplicación |
| MikroTik RouterOS v7 | Preparar REST, tabla FIB y ruta VPN; solo aplicación |
| Router incompatible o sin política adecuada | Proponer gateway virtual y explicar requisitos Linux/LAN y cambio de gateway de clientes |
| Solo quiere evaluar | Mock; aclarar que no modifica tráfico |

Verificar capacidades concretas; la marca por sí sola no acredita compatibilidad.
Si falta la VPN, preguntar si prefiere prepararla en el router o valorar otra arquitectura.
Si el host no puede ofrecer macvlan/kernel Linux, detener esa vía y explicar qué recurso
falta. No levantar el sidecar como solución automática a un error de API.

Presentar un plan corto con driver, servicios, host, salida, clientes y cambios necesarios.
Resolver elecciones pendientes antes de ejecutar acciones que dependan de ellas. Si el
usuario ya autorizó ese alcance, continuar sin repetir confirmaciones rutinarias.

## Etapa 3 — Preparar el acceso y la distribución

Preguntar si usará acceso LAN directo o un proxy HTTPS existente y qué puerto quiere.
Elegir una release pública concreta si existe; comprobar assets y descarga anónima de
las imágenes requeridas. No suponer que `latest` existe porque el repositorio sea público.
Si no hay release, ofrecer compilación del checkout explicando que requiere Git/build.
Seguir INSTALLATION y conservar ambos archivos Compose en instalaciones compiladas.

Con router compatible iniciar **solo `tebypass`**, sin perfil gateway ni macvlan.
No subir WireGuard al panel en esta modalidad: el túnel reside en el router.
Usar un único worker. Configurar certificado/ruta del proxy solo si entra en el alcance;
debe conservar Host y apuntar al puerto interno 9384. El login detecta su origen, sin
necesidad de pedir IPs de proxy ni ejecutar `setup_proxy.py` para usar el panel.

## Etapa 4A — Router compatible

Guiar los preparativos de [Operación](OPERATIONS.md#preparación-por-router).
Pedir que introduzca credenciales en Ajustes, leer configuración antes de modificarla,
seleccionar salida y probar conexión. No inventar IDs ni nombres de tablas/reglas.
No sustituir el driver por Mock para hacer pasar la comprobación.
Si cambia de router, TeByPass debe poder retirar la política anterior; ante fallo,
recuperar su conectividad antes de insistir. No borrar reglas ajenas.

Continuar en la etapa 5. Esta vía no necesita la etapa 4B.

## Etapa 4B — Solo gateway virtual

1. Inspeccionar en el host Linux `ip -br link`, `ip -br addr` e `ip route`. Identificar
   con el usuario interfaz/bridge LAN, subred y router. No adivinar la interfaz padre.
2. Preguntar el rango DHCP y la IP deseada, o proponer una comprobación acotada autorizada.
   Confirmar disponibilidad con ARP y tabla DHCP; falta de respuesta ICMP no prueba IP libre.
   Elegir fuera del pool dinámico y fijar una MAC local determinista sin colisiones.
3. Indicar explícitamente, sustituyendo los marcadores solo en la sesión privada:
   «He asignado la IP <IP_GATEWAY> al Gateway Virtual. Por favor, crea una reserva fija
   en el servidor DHCP de tu router asociada a la MAC <MAC_GATEWAY>, y ponle una nota
   como 'TeByPass-Gateway' para no olvidarte». Si el DHCP no permite reservar fuera del
   pool, acordar una exclusión/asignación estática equivalente que evite colisiones.
4. Crear macvlan y `.env` local según OPERATIONS; conservar permisos restrictivos.
   Arrancar aplicación y sidecar con `--profile gateway` y el modo de distribución elegido.
5. Seleccionar `virtual_gateway`, subir `wg0.conf` en el panel y comprobar aplicación.
   Un 502 significa guardado pero no aplicado; resolverlo antes de continuar. No admitir
   hooks ejecutables. Table=off y el DNS del cliente se gestionan según OPERATIONS.
6. Comprobar `/api/v1/status` autenticado: driver `virtual_gateway`, salud y transporte
   `unix_socket`. Verificar desde otro equipo LAN. Ambos servicios comparten host/volumen;
   no anunciar soporte para sidecar remoto.
7. Guiar primero un cliente de prueba: IP/máscara válidas, gateway igual a la IP del sidecar,
   DNS habitual accesible. Para macOS, proporcionar ruta temporal y su comando de retirada;
   para Linux, adaptar `ip route` a su ruta previa y documentar restauración. Para TV/deco,
   anotar los ajustes anteriores antes de pasar a IP manual. No cambiar todos los clientes
   a la vez. Evitar aislamiento Wi-Fi/red de invitados durante la prueba.

## Etapa 5 — Validar y entregar

- Acceso y login; si hay HTTPS, comprobar guardado desde esa URL. Un error de origen
  requiere revisar Host, no desactivar CSRF ni habilitar confianza global de proxy.
- Driver correcto y conexión real; `/healthz` solo acredita el proceso HTTP.
- Destino de prueba controlado: sincronización, política observada y salida desde cliente.
- Destino no seleccionado: mantiene salida habitual.
- Pausa: retira lo gestionado; reactivar restaura. Con gateway fail-closed, comprobar
  caída del túnel para destinos seleccionados y recuperación sin atribuir esa garantía
  a los demás drivers. No interrumpir un túnel compartido sin acordar esa prueba.
- Fuentes: activar únicamente las elegidas tras validar; explicar retención DNS,
  snapshot conservado en errores y diferencia entre pausa global y pausa de feed.
- Persistencia tras recreación cuando sea viable; copia consistente de BD y secretos,
  versión instalada y procedimiento de actualización/rollback del modo elegido.

Entregar resumen privado breve: componentes desplegados, acceso, driver/salida,
comprobaciones realizadas y pendientes, ubicación de copias y cómo revertir los cambios
del cliente. No incluir tokens. Si no se probó desde un cliente, declararlo pendiente;
no presentar mocks, ping o handshake como prueba de navegación por VPN.
