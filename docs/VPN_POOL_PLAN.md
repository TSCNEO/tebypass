# Plan: continuidad mediante varias salidas VPN

Estado: propuesta, sin implementar. Base: TeByPass 0.2.1.
Prioridad elegida: cambiar automáticamente cuando falla la VPN principal.

## 1. Entrega propuesta

Primera entrega: un pool de dos salidas, principal y reserva, para la política actual.
La aplicación conserva destinos, feeds y controles existentes. No reparte conexiones
entre VPNs en esta fase. Debe detectar fallo, seleccionar una reserva comprobada,
confirmar la política efectiva y explicar el cambio en el panel.

Después se añaden selección por servicio y rendimiento; el reparto por conexiones
queda como una entrega independiente. Un usuario con una sola VPN mantiene el
comportamiento actual, sin sondas externas nuevas habilitadas automáticamente.

Objetivos de aceptación iniciales, pendientes de validar en laboratorio:

- Perfil rápido: sonda ligera cada 10 s, timeout 3 s, caída tras 3 fallos consecutivos.
  Objetivo de conmutación ≤ 45 s desde el fallo, con control disponible y reserva lista.
- Recuperación: 6 éxitos consecutivos y al menos 120 s de estabilidad antes de volver.
- Retorno automático configurable; valor inicial: continuar en reserva hasta retorno
  manual, para evitar otra interrupción. Un fallo de la reserva permite buscar otra sana.
- Si todas fallan: bloquear destinos seleccionados cuando se haya elegido fail-closed;
  salida normal solo mediante opción fail-open explícita y soportada.
- El resto del tráfico conserva su recorrido. El cambio puede cortar conexiones
  existentes por cambio de IP pública/NAT; no se promete migración transparente.

Estos tiempos son presupuestos de diseño, no garantías de la versión actual. Deben
medirse incluyendo tiempo de detección, API del router y verificación de escritura.

## 2. Qué significa que una salida funciona

Separar tres resultados, con fecha y contexto de red:

1. **Túnel/control:** interfaz, estado nativo y handshake cuando existan. No prueban
   acceso a Internet por sí solos ni penalizan un túnel ocioso solo por handshake viejo.
2. **Conectividad:** TCP/TLS o HTTPS por esa salida hacia endpoints configurados,
   con comprobación de respuesta, pérdida/timeout y latencia observada.
3. **Servicio:** respuesta esperada del destino de interés por esa salida. Un 403,
   login o fallo aislado se clasifica; no se etiqueta automáticamente como bloqueo VPN.

Estados de transporte: unknown, healthy, suspect, down, recovering y draining.
Resultado de servicio independiente: pass, fail, auth_required o inconclusive.
Una VPN puede estar sana globalmente y fallar para un servicio concreto.

Para la primera entrega, usar al menos dos comprobaciones ligeras independientes
configuradas por el administrador y un objetivo con respuesta determinista bajo su
control. Con dos objetivos, ambos deben fallar durante las rondas exigidas para declarar caída;
fallar solo uno produce aviso. Si el mismo objetivo falla por todas las salidas,
clasificarlo como inconcluso/posible fallo del objetivo y evitar rotaciones en cascada.
Un único servidor caído no debe provocar una cascada de cambios de VPN.
Un ping puede ser diagnóstico opcional: si no responde a ICMP, no demuestra fallo HTTPS.
No lanzar tests de velocidad continuos; ocupan el enlace y distorsionan las mediciones.

Latencia: guardar tiempo TCP/TLS/TTFB y tasa de éxito por salida; distinguirlos de RTT
ICMP. Usar ventana móvil, percentiles y datos recientes. En fase inicial, latencia alta
avisa/degrada; solo provoca cambio con umbral sostenido configurable. La continuidad
no debe convertirse en una carrera para perseguir el menor ping de cada muestra.

## 3. Sondas que realmente salgan por la VPN

### Gateway virtual

El sidecar ejecuta sondas en un contexto de salida fijado a cada túnel y sin fallback
por la tabla principal. Comprobar en laboratorio interfaz, marca/ruta y salida observada.
Si se utiliza un endpoint de diagnóstico de IP, debe ser configurable; un resultado
cacheado o sin IP de salida conocida no cuenta como demostración de recorrido.

Los endpoints de transporte WireGuard y el tráfico de control nunca se seleccionan
recursivamente para viajar dentro del mismo túnel. Resolver también las dependencias
DNS del endpoint fuera de esa recursión. Para medir DNS del servicio, usar el contexto
correspondiente y respetar hostname/SNI en TLS; consultar IP sin SNI no es prueba equivalente.

### Router compatible

La aplicación no puede asumir que su propio tráfico pasa por cada VPN del router.
Necesita una de estas capacidades verificadas:

- Diagnóstico nativo que permita elegir la salida y observar un resultado útil.
- Origen de sonda dedicado por salida, con política de origen preparada en el router
  y observación de que realmente se usa la VPN elegida.
- Agente de sondas opcional en la LAN, con identidades de origen separadas y autenticación.
  Su configuración no exige convertirlo en gateway de los clientes.

Una métrica del controlador sin contexto de salida se muestra como tal, no como prueba
extremo a extremo. Si no puede acreditarse el recorrido, mostrar unknown y no activar
conmutación basada en esa medición. Nunca probar una reserva cambiando temporalmente
la política de todos los clientes de producción.

Un router compatible sigue sin necesitar el gateway virtual de TeByPass. Si requiere
agente de sondas, el asistente debe explicar esa dependencia distinta antes de desplegarlo.

## 4. Plan por backend

Las siguientes son vías de implementación, no compatibilidad ya disponible en TeByPass.

| Backend | Primera vía de continuidad | Condición de aceptación |
|---|---|---|
| UniFi | Pool de clientes VPN existentes; cambiar salida de la Traffic Route gestionada | Probar cambio de network_id, kill-switch, sonda por salida y comportamiento de sesiones en el modelo/firmware real |
| Omada | Pool de salidas VPN existentes; cambiar destino de política mediante Open API si lo permite | Prototipo de API por tipo de túnel/modelo; balanceo WAN no demuestra balanceo ni failover de VPN |
| OPNsense | Integrar grupo de gateways previamente preparado, con prioridad/reserva y monitorización nativa | Verificar que los gateways VPN son elegibles y que regla/grupo se observan por API; el driver actual no gestiona grupos |
| MikroTik | Tablas de salida por VPN y reglas gestionadas, o failover nativo preparado | Comprobar FIB, ruta efectiva, prioridades, FastTrack y sondas del recorrido real; el driver actual solo usa una tabla |
| Virtual | Varios túneles WireGuard, salida activa y reserva listas | Tablas/marcas aisladas, NAT/conntrack correctos, política efectiva y rollback; el runtime actual solo usa wg0 |
| Mock | Reloj y fallos deterministas | Simular transiciones y confirmación sin presentar conectividad real |

El fallback nativo de un router hacia WAN no equivale a elegir otra VPN. El modo
fail-closed tampoco implica por sí mismo continuidad entre túneles.

Definir un único responsable de selección por pool: TeByPass o grupo nativo del router.
No combinar dos bucles que cambien la salida por su cuenta. Si el selector es nativo,
TeByPass observa/configura lo soportado y muestra esa delegación de forma explícita.

La caída de la API/router no significa que la VPN de datos esté caída, pero impide
confirmar una modificación. Conservar estado observado y reportar resultado desconocido;
no afirmar que hubo conmutación si no puede comprobarse. Si se necesita continuidad
con TeByPass parado, usar selector nativo comprobado o supervisor local del sidecar
con política declarativa acotada. No prometerla en backends con selector solo en el core.

## 5. Arquitectura sin monolito

Extender contratos antes de implementar reglas por fabricante:

- `VpnExit`: identidad estable, backend, referencia a VPN/tabla/grupo existente o perfil
  WireGuard, habilitación y referencia privada a credenciales. No incrustar secretos en eventos.
- `VpnPool`: miembros, prioridad/peso, modo, umbrales, retorno y conducta si no hay salida.
- `ProbeProfile`: objetivos, protocolo, respuesta esperada, frecuencia y contexto requerido.
- `ProbeResult`: resultado, tiempos, salida/contexto acreditados, fecha y motivo clasificado.
- `SelectionDecision`: salida actual/propuesta, evidencias, motivo y generación de política.
- `RoutingPolicy`: selección deseada y destinos por política; no limitar el contrato a
  un set de CIDRs sin salida. El core no conoce comandos ni APIs de fabricantes.
- `DriverCapabilities`: varias salidas, selector nativo, sondas fijadas a salida,
  fail-closed verificable, bloqueo durante cambio, afinidad, métricas y familias IP.

Módulos separados: catálogo/persistencia de salidas; programación y ejecución de sondas;
selector puro; reconciliación de política; adaptadores de router; runtime de túneles.
La API consume servicios y los drivers no consultan BD. No dar socket Docker al panel.

Sondas fuera del lock de escritura de rutas: concurrencia acotada, timeout/cancelación,
instantáneas inmutables y resultados con generación. Antes de aplicar, revalidar que la
configuración no cambió. Un resultado viejo o una respuesta tardía del agente no cambia
la salida nueva. Serializar aplicación y rollback; no retener el lock mientras se espera
una batería de sondas. Mantener un solo worker de control.

Persistencia: migraciones aditivas para exits, pools, perfiles, estado de salud/selección
y eventos. Datos de sondas con retención acotada, sin crecimiento indefinido. Migrar la
salida actual a un pool manual de un miembro, conservando pausa, destinos y secretos.
No levantar una reserva ni cambiar el driver automáticamente durante la migración.

## 6. Conmutación y estabilidad

Flujo: observar → confirmar fallo → comprobar reserva → preparar política → aplicar →
verificar recorrido/política → registrar selección. El estado deseado se separa del
aplicado. Persistir identidad de la política y evidencia; reconciliar tras reinicio.

Si el backend permite preparar antes de activar, hacerlo. Si no, mantener bloqueo
explícito durante el cambio cuando el contrato exija fail-closed. Sin esa capacidad,
no anunciar cambio sin fugas: el modo protegido queda no disponible hasta validar una
estrategia en ese driver. La reserva no obtiene datos de clientes hasta estar seleccionada.

Cambiar a reserva sana ante fallo confirmado; drenar cambios voluntarios para conexiones
nuevas cuando el backend pueda conservar afinidad. Al fallar un túnel, las conexiones que
lo usaban pueden requerir reconexión. Limpiar solo los estados gestionados afectados,
si está soportado y se ha elegido esa política; no vaciar conntrack global.

Cooldown, muestras consecutivas y mínimos de permanencia evitan oscilaciones. Diferenciar
fallo de túnel, pérdida de conectividad, caída del servidor de prueba, error de autenticación
y fallo de servicio. Si todas las salidas son desconocidas, no tratarlas como saludables.

## 7. Gateway con varios túneles

Prototipo inicial con dos interfaces WireGuard, tablas/marcas por salida, conjuntos
separados y checkpoint versionado de política. Preservar IP/MAC de entrada del gateway:
los clientes no necesitan cambiar su puerta de enlace cada vez que cambia la VPN.

Antes de cerrar la implementación, probar dos perfiles del mismo proveedor con dirección
interna repetida, rutas/DNS solapados y AllowedIPs amplios. Si la separación mediante
tablas no garantiza aislamiento, usar namespaces/contextos por salida. Crear namespaces
puede requerir permisos adicionales: verificar mecanismo concreto y declararlo en Compose;
no añadir contenedor privileged ni SYS_ADMIN por defecto sin justificarlo y comprobarlo.

Cada túnel conserva última configuración aplicada y rollback independiente. Recrear o
fallar la reserva no debe tirar la principal. El socket Unix intercambia perfiles/políticas
validados; no acepta comandos shell arbitrarios. Reinicio restaura estado bajo bloqueo
hasta completar comprobación de rutas y protección.

## 8. Fases posteriores: servicio y reparto

### Elección por servicio

Asociar un perfil de comprobación a un conjunto de destinos: prueba TCP/TLS/HTTP y
respuesta esperada, con hostname correcto y contexto de salida comprobado. No asumir que
un código HTTP indica la causa del fallo. Sin comprobación válida, mostrar inconclusive.
Validar una operación pequeña y representativa; la portada accesible no acredita todas
las dependencias de una aplicación. No guardar tokens de sesión ni cuerpos privados en logs.

Una VPN no apta para un servicio puede seguir siendo apta para otros. Para eso se requiere
particionar políticas: preferencia explícita de destino, pool de lista/feed y pool por
defecto, con conflictos visibles antes de aplicar. No agregar CIDRs entre salidas distintas.
Prefijos solapados usan precedencia definida y conjuntos disjuntos; dos FQDN compartiendo
IP no pueden distinguirse por hostname con routing L3: solicitar una decisión o usar la
misma salida. No prometer selección distinta por URL.

### Reparto entre VPNs

Solo después: reparto ponderado de conexiones nuevas con afinidad. El modo conservador
puede fijar cliente o grupo de servicio a una salida durante una ventana; repartir por
flujo se habilita explícitamente cuando varias conexiones de una misma aplicación pueden
usar distintas IPs públicas sin romperla. TCP/UDP/QUIC se incluyen en los ensayos.

En virtual, marcas de conexión y NAT mantienen el recorrido de cada flujo. En router,
usar únicamente mecanismos nativos realmente expuestos y verificados. No cambiar la VPN
paquete a paquete. No prometer sumar el ancho de banda para una conexión individual;
para eso haría falta cooperación en ambos extremos y otra arquitectura.

## 9. Interfaz y criterios de salida

Pantalla Salidas: principal/reserva, estado de túnel, conectividad, latencia, evidencia de
sonda, último cambio y motivo. Botones Probar salida, Forzar reserva, Volver a principal
y Mantener actual; las acciones indican si guardan o aplican políticas. Modo observación
inicial sin cambios de rutas y previsualización antes de activar automatismo.

Pruebas obligatorias antes de publicar cada fase:

- Principal sana no conmuta; pérdida y recuperación sostenidas cumplen ventanas.
- Túnel presente sin tráfico, DNS roto, ICMP filtrado y endpoint de prueba caído.
- Reserva sana y reserva inaccesible; ambas caídas; todas unknown; pausa global.
- API caída, ACK perdido, escritura parcial, rollback fallido y resultado de sonda tardío.
- Reinicio de app y sidecar, configuración cambiada durante sondeo y migración de 0.2.1.
- Tráfico seleccionado/no seleccionado; fail-closed sin escape durante cambio, DNS y
  rutas de control correctos. Pausa/purga retiran todas las reglas y marcas gestionadas.
- Dos VPNs de laboratorio: caída forzada de principal y recuperación verificadas con
  clientes reales TCP/UDP/QUIC y captura sanitizada, no solo estado de interfaz.
- Configuraciones de IP interna duplicada y rutas solapadas, carga y límites de sondas.
- Cada driver anunciado necesita evidencia de cambio y recorrido en equipo/firmware
  concreto; no usar mocks como certificación de hardware.

## 10. Entregas y puertas de decisión

1. **Descubrimiento técnico:** matriz por driver y prototipo de sonda con salida fijada.
   Entregable: evidencia de que la reserva se puede medir sin alterar tráfico de clientes.
2. **Catálogo y modo observación:** pools, migración de un miembro, estado y eventos,
   sin conmutación automática. Validar clasificación y estabilidad con fallos inyectados.
3. **Continuidad inicial:** virtual y primer driver de router con capacidades acreditadas
   (candidato: UniFi); dos VPNs, prioridad/reserva y política de todas caídas.
4. **Ampliación de routers:** Omada, OPNsense y MikroTik según resultado del prototipo,
   con adaptación nativa cuando corresponda y límites publicados.
5. **Selección por servicio:** perfiles, matriz salida/servicio y resolución de conflictos L3.
6. **Reparto:** pesos, afinidad, drenaje y métricas de carga, solo donde esté soportado.

No fijar fechas ni afirmar paridad de routers antes del descubrimiento. La primera
funcionalidad útil será continuidad con dos VPNs; se puede entregar sin esperar al
balanceo. No se modifica la instalación existente como parte de este plan.

## Fuentes primarias consultadas (2026-11-10)

- [UniFi Policy-Based Routing](https://help.ui.com/hc/en-us/articles/12566175125783-UniFi-Gateway-Policy-Based-Routing):
  selección de interfaz y kill-switch; no demuestra por sí sola sondas por cada VPN.
- [Omada Policy Routing](https://support.omadanetworks.com/za/document/50367/):
  distingue políticas/prioridad y estado de WAN; no acredita equivalencia para todos los túneles VPN.
- [OPNsense Gateway groups](https://docs.opnsense.org/manual/multiwan.html):
  prioridades, latencia/pérdida y reparto nativos, con requisitos de gateway elegible.
- [MikroTik PCC](https://help.mikrotik.com/docs/spaces/ROS/pages/152600617/Per%2Bconnection%2Bclassifier):
  distribución por campos de conexión; requiere configurar marcas/routing apropiados.
- [WireGuard namespaces](https://www.wireguard.com/netns/): aislamiento de contextos de red.
- [nftables connection marks](https://wiki.nftables.org/wiki-nftables/index.php/Setting_packet_metainformation):
  persistencia/restauración de marcas de conexión.

Los mecanismos documentados por fabricantes no prueban que estén disponibles en los
endpoints usados por TeByPass. Las vías de adaptación y las fases anteriores son diseño
propuesto, sujetas a verificación de API y tráfico.
