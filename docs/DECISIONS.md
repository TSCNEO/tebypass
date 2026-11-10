> Registro histórico. Para el estado vigente prevalecen ADR-013, README y PHASES.md; las capacidades de diseños anteriores no implican implementación o validación actual.

# TeByPass — Registro de Decisiones de Arquitectura (ADR)

Este documento recopila las decisiones de arquitectura formalizadas (Architecture Decision Records) que rigen el diseño y la evolución técnica de **TeByPass**.

---

## ADR-001: Arquitectura Modular Desacoplada ("0 Monolitos") frente a Monolito Tradicional

### Estado
**Aceptado**

### Contexto
Muchas herramientas de red existentes empaquetan en un único script o binario la descarga de feeds, el scraping web, la resolución DNS, el servidor HTTP, el dashboard y la manipulación de iptables/enrutamiento. Esto genera una deuda técnica inmanejable: cualquier cambio en la API del router rompe el core, el testing automatizado exige privilegios de root y hardware físico, y el despliegue en routers externos (UniFi, MikroTik) resulta inviable sin modificar todo el código.

### Decisión
Adoptar una separación estricta en capas desacopladas:
- **Core Engine**: Orquestación y algoritmos independientes del entorno de red.
- **Drivers de Enrutamiento**: Implementaciones intercambiables bajo la interfaz `BaseRouterDriver`.
- **Capa Web/API**: Consumidor puro del Core Engine.
- **Sidecar de Red**: Contenedor aislado para el plano de datos.

### Consecuencias
- **Positivas**:
  - Posibilidad de testear el 100% de la lógica sin privilegios de red mediante `MockRouterDriver`.
  - Capacidad de soportar múltiples fabricantes (UniFi, MikroTik, Linux estándar) simplemente añadiendo un nuevo driver.
  - El proceso web/API se ejecuta con usuario sin privilegios (`non-root`).
- **Negativas / Trade-offs**:
  - Mayor número inicial de archivos y abstracciones formales.

---

## ADR-002: FastAPI y SQLite en Modo WAL frente a Frameworks Pesados y RDBMS Externos

### Estado
**Aceptado**

### Contexto
TeByPass está diseñado para operar en entornos heterogéneos, desde servidores dedicados hasta dispositivos Edge (Raspberry Pi, mini PCs, contenedores LXC con recursos limitados). El sistema necesita persistencia fiable para miles de registros, transacciones ACID y lecturas concurrentes rápidas sin obligar al usuario a desplegar y mantener servidores PostgreSQL o MySQL.

### Decisión
Utilizar **FastAPI** (Python 3.12+) con **SQLite 3** configurado explícitamente en modo **WAL (Write-Ahead Logging)** y sincronización normal (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`), operado mediante **SQLAlchemy 2.0** asíncrono con el driver `aiosqlite`.

### Consecuencias
- **Positivas**:
  - Cero dependencias externas de bases de datos: despliegue mediante un único archivo local.
  - Modo WAL permite lecturas concurrentes sin bloqueo mientras el motor escribe actualizaciones de rutas en segundo plano.
  - Consumo de memoria del proceso inferior a 60 MB.
- **Negativas / Trade-offs**:
  - No apto para escalado horizontal activo-activo de múltiples instancias de control (diseño enfocado a single-controller por red o clúster local).

---

## ADR-003: L3 Split-Routing frente a Proxy de Capa 7 (HTTP/SOCKS5)

### Estado
**Aceptado**

### Contexto
El desvío de tráfico de streaming y servicios en directo puede implementarse en Capa 7 (proxies HTTP transparentes, Envoy, Squid, SOCKS5) o en Capa 3 (enrutamiento IP selectivo mediante tablas y túneles VPN). Los proxies L7 sufren problemas graves con tráfico TLS: requieren certificados raíz falsificados (MITM) que rompen DRM Widevine/FairPlay, introducen latencia de CPU al reenvasar paquetes TCP, y no todos los reproductores admiten configuración de proxy.

### Decisión
Implementar el desvío estrictamente en **Capa 3 (L3 Split-Routing)** a través de WireGuard y reglas de firewall (`nftables` / routing tables). El tráfico IP hacia los destinos catalogados se conmuta a nivel de paquete sin terminar ni inspeccionar las sesiones TLS.

### Consecuencias
- **Positivas**:
  - Cero sobrecarga de descifrado o reensamblado: el rendimiento lo marca el hardware de red y el protocolo de túnel WireGuard (en espacio de kernel o acelerado).
  - Totalmente transparente para DRM, TLS 1.3, HTTP/2, HTTP/3 (QUIC) y WebSockets.
  - Los clientes en la red no requieren configuración de certificados ni agentes locales.
- **Negativas / Trade-offs**:
  - Exige que el router o gateway de la red admita enrutamiento de políticas o que los clientes apunten al sidecar como puerta de enlace.

---

## ADR-004: Resolución DNS Nativa del Host (`getaddrinfo`) frente a Resolvers Upstream Hardcodeados

### Estado
**Aceptado**

### Contexto
Muchas aplicaciones de evasión de bloqueos fuerzan servidores DNS públicos en su código (como fijar `8.8.8.8` o `1.1.1.1`). En entornos corporativos o redes domésticas avanzadas con servidores DNS locales (AdGuard Home, Pi-hole, Unbound, split-horizon DNS), esta práctica causa fugas de privacidad, resolución de IPs geográficamente incorrectas de CDNs y conflictos de resolución interna.

### Decisión
Utilizar la llamada estándar asíncrona delegada en la biblioteca del sistema operativo (`asyncio.get_running_loop().getaddrinfo`). TeByPass nunca forzará resolvers fijos en su código fuente.

### Consecuencias
- **Positivas**:
  - Respeta íntegramente la configuración DNS del host o contenedor (`/etc/resolv.conf`).
  - Compatibilidad total con resolvers de filtrado local y zonas DNS privadas.
  - Resolución dual IPv4 (`A`) e IPv6 (`AAAA`) automática.
- **Negativas / Trade-offs**:
  - Si el resolver del host está corrupto o mal configurado, la resolución fallará (se mitiga reportando el estado en el endpoint `/api/v1/status`).

---

## ADR-005: Caché de Ventana Deslizante (Sliding-Window TTL) frente a Caché Estática

### Estado
**Aceptado**

### Contexto
Los proveedores de CDNs y balanceadores de carga asignan a menudo TTLs extremadamente breves a los registros DNS (ej. 30 a 60 segundos). Si las rutas en la tabla del router expiran estrictamente con el TTL de la consulta DNS inicial, las conexiones de streaming activas sufren microcortes al cambiar de nodo o al purgarse la ruta del router en mitad del evento.

### Decisión
Implementar un algoritmo de **Ventana Deslizante (Sliding-Window TTL)** en el Core Engine (`app.core.cache`). Cada vez que un objetivo sigue activo en las listas o se detecta resolución consecutiva, su caducidad en el enrutador se extiende dinámicamente un periodo de gracia configurable (por defecto 3,600 segundos = 1 hora):
$$\text{ExpireAt} = \max(\text{Now} + \text{DNS\_TTL}, \text{LastSeen} + \text{WindowExtension})$$

### Consecuencias
- **Positivas**:
  - Estabilidad continua del streaming sin interrupciones abruptas durante eventos en directo.
  - Purga automática de destinos obsoletos tras un periodo de inactividad sin intervención manual.
- **Negativas / Trade-offs**:
  - La tabla de rutas puede retener IPs durante la ventana de extensión aunque el servicio remoto haya cambiado de dirección.

---

## ADR-006: Sidecar Alpine + nftables + WireGuard frente a OpenWRT / Monolitos Tipo Gluetun

### Estado
**Aceptado**

### Contexto
Para usuarios que no poseen routers empresariales (UniFi o MikroTik), se requiere una solución de gateway virtual local. Desplegar una máquina virtual completa de OpenWRT o reutilizar contenedores monolíticos diseñados para VPN completa (como Gluetun) introduce complejidad innecesaria, consumo elevado de recursos y dificultades para configurar split-routing fino en Capa 3.

### Decisión
Construir una imagen sidecar específica (`gateway/`) basada en **Alpine Linux minimalista**, utilizando la suite nativa moderna del kernel Linux: `nftables`, `iproute2` y `wireguard-tools`.

### Consecuencias
- **Positivas**:
  - Tamaño de imagen minúsculo (< 30 MB) y arranque en milisegundos.
  - Uso de conjuntos dinámicos de nftables (`nftables sets`) para búsquedas en tiempo constante $O(1)$ sobre miles de prefijos IP.
  - Desacoplamiento total del ciclo de vida del contenedor web: el gateway puede reiniciarse o actualizarse independientemente.
- **Negativas / Trade-offs**:
  - Requiere privilegios de red en el contenedor de gateway (`NET_ADMIN` y `/dev/net/tun` o módulo de kernel WireGuard).

---

## ADR-007: Configuración Operativa Persistida en SQLite (`app_settings`) con Hot-Reload frente a Variables de Entorno

### Estado
**Aceptado**

### Contexto
Obligar al usuario a editar un archivo `.env` o el `docker-compose.yml` y recrear el contenedor para cambiar el driver del router, una API key, el intervalo de sondeo o activar el bypass de un operador va en contra de la usabilidad del producto homelab. Toda la configuración debe ser modificable desde la propia interfaz web y tener efecto inmediato sin cortes de servicio.

### Decisión
Trasladar toda la configuración operativa a una tabla `app_settings` en la base de datos SQLite. Los archivos `.env` y `docker-compose.yml` se reservan exclusivamente para las dos variables bootstrap inmutables en tiempo de ejecución: el puerto de escucha (`TEBYPASS_PORT`) y la ruta a la base de datos (`TEBYPASS_DATABASE_URL`). Las modificaciones en la web invalidan en caliente las instancias de drivers y actualizan los intervalos del scheduler sin reiniciar el contenedor.

### Consecuencias
- **Positivas**:
  - Experiencia 100% amigable: el usuario gestiona su homelab desde el navegador.
  - El archivo `docker-compose.yml` queda limpio y neutro, sin credenciales de routers ni URLs de infraestructura.
- **Negativas / Trade-offs**:
  - Exige una estrategia rigurosa de enmascarado y cifrado en reposo para credenciales almacenadas en SQLite.

---

## ADR-008: Cifrado Simétrico en Reposo (AES-128-CBC + HMAC-SHA256 Fernet) para Credenciales de Red

### Estado
**Aceptado**

### Contexto
A diferencia de las contraseñas de usuarios (que se aseguran con hashes unidireccionales como bcrypt), las API keys de UniFi o contraseñas de MikroTik son secretos de salida que TeByPass debe enviar a los routers para autenticar las peticiones. Si se almacenan como hashes, el valor se destruye y la integración es imposible. Si se almacenan en texto claro en SQLite, una copia de seguridad expone las credenciales de la red.

### Decisión
Implementar cifrado simétrico autenticado en reposo mediante **Fernet (AES-128-CBC con PKCS7 padding y firma HMAC-SHA256)** para todo registro marcado con `is_secret=True`.
- La clave maestra se guarda en `/data/.secret_key` con permisos `chmod 600` en el volumen de datos o se deriva opcionalmente de `TEBYPASS_SECRET_KEY`.
- Todo valor cifrado lleva el prefijo de versión `enc:v1:`.
- Los secretos existentes en texto claro se detectan en tiempo de carga, se descifran limpiamente y se re-encriptan de manera transparente en la base de datos (migración en caliente con cero tiempo de inactividad).

### Consecuencias
- **Positivas**:
  - Máxima seguridad para credenciales de infraestructura en reposo: una filtración del archivo SQLite no compromete los routers.
  - Compatibilidad total con las necesidades operativas de los drivers (descifrado en memoria sólo cuando se requiere).
- **Negativas / Trade-offs**:
  - Si se pierde el archivo `/data/.secret_key` y no se configuró `TEBYPASS_SECRET_KEY`, las credenciales deben reintroducirse en la web.

---

## ADR-009: Licencia GNU Affero General Public License v3 (AGPL-3.0-or-later)

### Estado
**Aceptado**

### Contexto
La licencia permisiva MIT original permitía la explotación comercial directa de TeByPass por parte de terceros sin compartir mejoras con la comunidad ni liberar el código fuente de derivados alojados en la nube. Se requería una licencia que protegiera el carácter libre, comunitario y no comercial del proyecto sin impedir el uso y modificación personal.

### Decisión
Adoptar la **GNU Affero General Public License v3 (AGPL-3.0-or-later)** para el repositorio, el archivo `pyproject.toml` y toda la documentación.

### Consecuencias
- **Positivas**:
  - Garantiza que cualquier bifurcación, modificación o servicio alojado en red deba entregar su código fuente íntegro a sus usuarios bajo la misma licencia.
  - Desincentiva el empaquetado privativo con fines de lucro por parte de empresas comerciales.
- **Negativas / Trade-offs**:
  - Incompatible con proyectos o librerías que requieran integración en software propietario cerrado.

---

## ADR-010: Sidecar con IP LAN propia (macvlan) y plano de control por socket Unix

### Estado
**Aceptado**

### Contexto
Para que el gateway virtual sirva a dispositivos reales, el sidecar debe ser su puerta de enlace, y eso exige una dirección propia en la LAN. Además, la aplicación necesitaba mutar el set de `nftables` del sidecar: exponer un puerto TCP para ello añadía superficie de ataque y un salto de red innecesario, y dar `NET_ADMIN` al contenedor de la aplicación mezclaba planos de datos y de control.

### Decisión
1. Conectar `tebypass-gateway` a una red **macvlan** sobre la interfaz física del host, con IP configurable (`TEBYPASS_GATEWAY_IP`). La aplicación permanece en la red bridge por defecto.
2. Comunicar ambos contenedores por un **socket Unix** en el volumen compartido (`/data/gateway.sock`, `0600`) hablado por `gateway/controller.py`.
3. El daemon valida cada prefijo con `ipaddress` y aplica los cambios como una única transacción `nft -f`, con reemplazo completo como reintento si el set de intervalos rechaza un delta.

### Consecuencias
- **Positivas**:
  - El contenedor de la aplicación no necesita privilegios de red ni binario `nft`: el sidecar es el único dueño del plano de datos.
  - Sin puerto TCP: nada que exponer, nada que autenticar en la red; los permisos del socket hacen el control de acceso.
  - Un fallo del daemon es visible de inmediato (`status` degradado) y el driver cae a transporte local o emulado en lugar de romper el ciclo de sincronización.
- **Negativas / Trade-offs**:
  - Las redes macvlan no permiten que el propio host anfitrión hable con el contenedor; las comprobaciones deben hacerse desde otro equipo de la LAN.
  - La red macvlan debe crearse una vez en el host antes de levantar el perfil `gateway`.

---

## ADR-011: Driver UniFi mediante API V2 Traffic Routes y descubrimiento de interfaces WireGuard/VPN

### Estado
**Aceptado**

### Contexto
En routers UniFi OS modernos (Cloud Gateway Ultra/Max/Fiber, Dream Machine Pro/SE, UniFi Express), los grupos de firewall tradicionales no crean rutas de política ni enrutan selectivamente el tráfico de clientes LAN hacia interfaces VPN específicas de WireGuard. StopLiga demostró que la vía canónica es la API V2 de Traffic Routes (`/v2/api/site/{site}/trafficroutes`), donde cada ruta asocia destinos IP (`matching_target: "IP"`) a una interfaz de cliente VPN (`network_id`), aplicando `target_devices: [{"type": "ALL_CLIENTS"}]`.

### Decisión
1. Reemplazar los stubs de UniFi con una implementación `httpx.AsyncClient` asíncrona real.
2. Descubrir automáticamente interfaces de cliente VPN en `/api/s/{site}/rest/networkconf` (`purpose: "vpn-client"`).
3. Gestionar la ruta de tráfico con `POST` para creación y `PUT /v2/api/site/{site}/trafficroutes/{id}` para sincronización idempotente del listado de IPs.
4. Exponer selector web en la UI con endpoint `/api/v1/settings/unifi/vpn-networks` para que el usuario elija dinámicamente qué interfaz WireGuard del router cursará el tráfico de los destinos bloqueados.

### Consecuencias
- **Positivas**:
  - Paridad funcional 100% con StopLiga y soporte nativo para redes con VLANs y UniFi Cloud Gateways.
  - Desacoplamiento total: no requiere agentes en los dispositivos clientes ni alterar el gateway por DHCP en redes con enrutador avanzado.
- **Negativas / Trade-offs**:
  - Requiere que el usuario tenga configurado al menos un cliente VPN/WireGuard en su UniFi Network.

---

## ADR-012: Control de rutas serializado y apagado verificable

### Estado
Aceptado; primera entrega de la auditoría funcional.

### Decisión
- Todas las reconciliaciones del proceso comparten un bloqueo de escritura. La API usa
  el motor vivo y su caché; guardar ajustes y purgar se coordinan con ese mismo bloqueo.
- Cada ciclo captura una instancia de driver. Los fallos de observación y apagado se
  registran como SyncEvent fallido; `total_active=-1` expresa estado desconocido.
- Mock es el único driver que simula escrituras. UniFi sin configuración, MikroTik aún
  sin implementación y gateway sin transporte no pueden acreditar éxito operativo.
- El contrato de teardown exige error explícito si no está soportado. UniFi verifica
  que no queden reglas propias; gateway envía `cmd=flush` y comprueba el set vacío.

### Consecuencias
Se sustituye el fallback emulado de ADR-010 por fallo explícito. ADR-011 describe el
primer soporte UniFi, pero su afirmación de paridad completa queda retirada: faltan los
formatos adicionales y las garantías identificadas en la auditoría. El contrato completo
por política y las transiciones entre drivers se desarrollarán en entregas posteriores.
El bloqueo es por proceso: el despliegue soportado usa un único worker de control.

---

## ADR-013: Alcance verificable del MVP 0.2

**Aceptado.** Las decisiones previas son históricas; las afirmaciones de paridad completa,
emulación de drivers operativos o sidecar remoto quedan sustituidas por este alcance.

- Dependencia stopliga fijada a `2ef9357702044367ff81b1bec51d77862c725764`, sin copiar
  destinos reales a fixtures. Se reutilizan señal global y backends Omada/OPNsense; se
  aíslan sus operaciones síncronas fuera del event loop y se verifica estado efectivo.
- Una política completa por instalación. Manuales y comunidad aportan a la misma unión,
  con activación comunitaria independiente y retención por destino persistida.
- Los endpoints CRUD delegan en servicios de core con errores independientes de HTTP.
- Gateway exclusivamente Unix/volumen local. El controlador controla WireGuard y nft;
  ruta unreachable como barrera fail-closed. El modo local/emulado queda retirado.
- Token administrativo aleatorio en volumen y sesiones firmadas. Assets locales y
  dependencias/hash fijados; versión del build en lugar de `.git` montado.
- Documentación vigente distingue implementación, pruebas sintéticas, pruebas de kernel
  y evidencia en hardware; ningún soporte nuevo se anuncia como certificado universalmente.


## ADR-014: Origen externo y confianza explícita del reverse proxy

**Aceptado.** El esquema HTTP del salto interno no representa necesariamente el origen
HTTPS del navegador. La confianza en cabeceras reenviadas se configura en el límite de
Uvicorn (`FORWARDED_ALLOW_IPS`), mediante `TEBYPASS_TRUSTED_PROXIES` de Compose, con
loopback como valor inicial. Es configuración bootstrap de despliegue, independiente de
los ajustes de routing de SQLite. No se habilita confianza indiscriminada en cabeceras.

La protección CSRF usa el origen efectivo y puertos predeterminados normalizados, sin
comparar rutas del proxy. Un origen ajeno y una cabecera de esquema enviada desde una
conexión no confiable siguen rechazándose. HTTPS reconocido produce cookie Secure en login.

## ADR-015: Configuración guiada del acceso HTTPS

**Aceptado.** La confianza del proxy sigue siendo bootstrap del host, pero no requiere
que el usuario conozca la dirección interna observada por Docker. Un asistente estándar
Python recibe la URL pública y consulta `/healthz`, que expone el esquema efectivo.
Cuando HTTPS externo aparece como HTTP interno, una petición con marcador aleatorio
permite identificar la conexión exacta en los logs de Uvicorn. Sin evidencia no se aplica.

El modo por defecto solo diagnostica; `--apply` conserva ajustes, crea copia privada,
recrea exclusivamente la aplicación y verifica. Si falla restaura la configuración y
recrea de nuevo, informando si la recuperación falla. No usa credenciales administrativas
ni habilita confianza global. Login y errores de guardado ofrecen instrucciones para
usuario o LLM. No configura certificados ni crea rutas del proxy: estos deben existir.

## ADR-016: Origen externo reconocido durante el login

**Aceptado; sustituye el requisito de configuración del proxy para usar la web de ADR-014/015.**
El formulario envía location.origin junto con la clave. Tras autenticar, se valida contra
Origin y la autoridad Host conservada por el proxy. Se firma dentro de una sesión v2 con
caducidad; no se registra globalmente ni se confía en cabeceras reenviadas de cualquier
cliente. Una visita anónima no configura nada. Cada escritura exige el mismo origen
completo, y HTTPS obtiene cookie Secure incluso sobre un salto interno HTTP.

Esto evita comandos o cambios bootstrap para navegar/guardar detrás de Traefik y admite
varios accesos con sesiones independientes. No altera TLS ni la configuración del proxy.
Los clientes API sin browser_origin conservan el formato previo; los agentes usan Bearer.
Las cookies anteriores siguen con su validación previa y se renuevan iniciando sesión.
El asistente de ADR-015 queda opcional para metadatos reenviados de conexión de Uvicorn.
La aplicación no obtiene acceso al socket Docker. Protección de origen basada en
[OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

## ADR-017: Distribución mediante imágenes verificadas de GHCR

**Aceptado.** El Compose de instalación referencia imágenes, no contiene build. Un override
explícito conserva el desarrollo desde código. App y sidecar se publican juntos para
amd64/arm64 tras CI; se descargan y prueban por build único antes de promover edge/version/latest.
La prueba de ejecución en GitHub es amd64. Las imágenes registran revisión y versión;
no necesitan Git en runtime. SBOM y procedencia acompañan a los manifiestos.

Un tag genera Compose fijado a la versión y borrador de release con assets/checksums.
No se crea ningún tag ni se cambia visibilidad como efecto de un push normal a main.
La visibilidad pública del código y de cada paquete, y el pull anónimo, son requisitos
previos a anunciar una instalación abierta. Gateway sigue necesitando topología macvlan:
el empaquetado elimina compilación, no puede inventar parámetros de la LAN.

## 2026-11-10 — Cierre de aceptación funcional para 0.2.1

- Las capacidades de destino se declaran en drivers/capabilities; los servicios validan
  ingesta y cambio de driver antes de persistir o retirar la política anterior. Rechazar
  feeds incompatibles conserva íntegro el último snapshot, con error visible.
- Refrescar un feed actualiza por identidad normalizada en vez de recrear todos los
  targets. Conserva la retención DNS de entradas que siguen presentes; elimina solo las
  retiradas. Fetch manual comparte el lock de reconciliación para evitar snapshots concurrentes.
- UniFi es propietario del alcance ALL_CLIENTS de su regla gestionada y verifica ese
  alcance tras escribir, además de IPs, salida y habilitación.
- El ACK de policy del sidecar exige verified y fail_closed coincidente; el sidecar
  observa regla exacta de marca/tabla y ruta unreachable. Actualizar aplicación y sidecar juntos.
- WireGuard conserva wg0.last-good.conf (0600) en el volumen después de aplicar túnel y
  ruta. Un fallo de actualización restaura el estado previo y comunica fallo; otro fallo
  al restaurar se comunica explícitamente. Borrar configuración elimina también el checkpoint.
  La configuración candidata puede seguir guardada tras un error: no confundir con la aplicada.
