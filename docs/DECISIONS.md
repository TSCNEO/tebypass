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
