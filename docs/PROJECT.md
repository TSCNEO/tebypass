# TeByPass — Documento de Proyecto

## 1. Visión y Objetivos

### 1.1 Declaración del Problema
En múltiples entornos de red, determinados proveedores de telecomunicaciones aplican medidas de filtrado y bloqueo indiscriminado a nivel de Capa 3 (IP/BGP) y Capa 7 (SNI/DNS) sobre rangos de direcciones que afectan a servicios legítimos y servicios en directo. Las soluciones convencionales presentan graves inconvenientes:
- **Túneles VPN completos (Full Tunnel)**: Desvían el 100% del tráfico del hogar o servidor por un túnel remoto, degradando el ancho de banda general, aumentando la latencia y disparando el consumo de datos innecesarios.
- **Proxies L7 (HTTP/SOCKS5)**: Introducen sobrecarga de desencapsulación TLS, rompen compatibilidad con clientes multimedia que no admiten configuración de proxy, y representan un cuello de botella en CPU.
- **Monolitos acoplados**: Aplicaciones que mezclan la descarga de listas, la resolución DNS, la interfaz de usuario y la inyección en routers en una única base de código rígida y difícil de mantener.

### 1.2 Misión de TeByPass
**TeByPass** proporciona un motor de derivación selectiva en Capa 3 (**L3 Split-Routing Engine**), modular, desacoplado y agnóstico. Permite que dispositivos clientes en la red local continúen navegando directamente a través de su conexión a Internet por defecto, desviando **única y exclusivamente** hacia un túnel seguro (WireGuard) los paquetes IP con destino a los rangos y dominios catalogados.

---

## 2. Alcance del Proyecto

### 2.1 En Alcance (In-Scope)
- Ingesta dual de objetivos (feeds remotos automatizados + objetivos estáticos definidos por el operador).
- Soporte para formatos estándar de la comunidad (StopLiga, HayAHora, listas en texto plano, JSON estructurado).
- Resolución DNS asíncrona mediante el resolver nativo del host (`getaddrinfo`), respetando la pila de resolución del sistema operativo y evitando resolvers forzados.
- Caché dinámica con ventana deslizante (sliding-window TTL) para estabilizar streams y evitar la pérdida de rutas durante transmisiones activas.
- Reconciliación idempotente de rutas en routers y firewalls: cálculo diferencial (`added`, `removed`, `unchanged`) para evitar saturación de tablas de estado.
- Arquitectura de drivers intercambiables:
  - Driver simulado para testing (`MockRouterDriver`).
  - Driver para Sidecar Gateway L3 (Alpine Linux + `nftables`).
  - Driver para routers UniFi (UniFi Network API / SSH ipset).
  - Driver para routers MikroTik (RouterOS v7 REST API / Address Lists).
- Panel de control web ligero y reactivo (FastAPI + Jinja2 + Alpine.js) en puerto por defecto `9384`.
- API REST documentada (OpenAPI / Swagger).
- Sidecar de gateway virtual L3 contenerizado (WireGuard + nftables nativo).

### 2.2 Fuera de Alcance (Out-of-Scope)
- Inspección profunda de paquetes L7 o descifrado TLS (MITM).
- Gestión integral de firewalls domésticos no relacionada con las rutas de desvío.
- Implementación propietaria de servidores VPN (se delega en WireGuard estándar).
- Interfaces dependientes de Node.js o frameworks SPA pesados.

---

## 3. Reglas de Negocio y Flujos Críticos

### 3.1 Flujo 1: Ingesta Dual (Dual Ingestion)
El sistema acepta dos fuentes convergentes de objetivos de red:
1. **Feeds Remotos Programados**:
   - URLs configuradas que exponen listas de dominios (`sport-stream.example.org`, `feed.example.net`) o rangos CIDR (`198.51.100.0/24`).
   - Se descargan mediante un scheduler periódico respetando encabezados HTTP (`ETag`, `If-Modified-Since`).
   - Admite mapeadores de formato: texto plano por líneas, JSON StopLiga y esquemas comunitarios.
2. **Objetivos Manuales / Dinámicos**:
   - Entradas registradas por el administrador a través de la API REST o la interfaz web.
   - Posibilidad de habilitar/deshabilitar objetivos individuales sin borrarlos.
   - Marcado de prioridad (las entradas manuales tienen persistencia garantizada).

```
[Feed Remoto (StopLiga/HayAHora)] ──┐
                                     ├──> [Motor de Ingesta] ──> [DB SQLite WAL (Targets)]
[Entradas Manuales del Operador]  ──┘
```

### 3.2 Flujo 2: Resolución DNS Nativa del Host
- **Principio**: Se rechaza explícitamente el uso de resolvers DNS públicos forzados en el código (como fijar `8.8.8.8` o `1.1.1.1`).
- **Implementación**: El motor ejecuta llamadas asíncronas no bloqueantes delegando en la biblioteca del sistema (`asyncio.get_running_loop().getaddrinfo`).
- **Beneficio**:
  - Respeta la configuración de red del anfitrión (incluyendo servidores DNS locales, servidores AdGuard Home / Pi-hole o resolvers de split-horizon).
  - Evita fugas y divergencias de resolución entre el contenedor y la infraestructura local.
  - Recoge tanto registros IPv4 (`A`) como IPv6 (`AAAA`).

### 3.3 Flujo 3: Caché Dinámica con Ventana Deslizante (Sliding-Window TTL)
- **Problema**: Los servicios de streaming o CDNs frecuentemente devuelven registros DNS con TTL muy bajos (ej. 30 o 60 segundos). Si una ruta en el router expira estrictamente al finalizar el TTL DNS, el stream sufre microcortes o desconexiones en mitad de un evento.
- **Mecanismo de Ventana Deslizante**:
  - Cada resolución almacena `first_seen_at`, `last_seen_at`, `ttl_seconds` y `window_extension_seconds`.
  - Si un dominio sigue siendo consultado o se mantiene en la lista de objetivos activos, la caducidad efectiva de la ruta en la tabla de enrutamiento se desplaza hacia adelante:
    $$\text{ExpireAt} = \max(\text{Now} + \text{DNS\_TTL}, \text{LastSeen} + \text{WindowExtension})$$
  - Se garantiza que ninguna ruta activa se elimine bruscamente durante la reproducción.

### 3.4 Flujo 4: Idempotencia en el Router
- **Principio**: La inyección y revocación de rutas debe ser estrictamente diferencial.
- **Ciclo de Reconciliación**:
  1. El Core Engine calcula el conjunto deseado de prefijos CIDR activos: $S_{\text{desired}}$.
  2. El Driver consulta el estado actual del dispositivo o tabla: $S_{\text{current}}$.
  3. Se calculan las diferencias:
     - $\Delta_{\text{add}} = S_{\text{desired}} \setminus S_{\text{current}}$
     - $\Delta_{\text{del}} = S_{\text{current}} \setminus S_{\text{desired}}$
  4. El driver ejecuta únicamente las operaciones atómicas sobre $\Delta_{\text{add}}$ y $\Delta_{\text{del}}$.
  5. Si $\Delta_{\text{add}} = \emptyset$ y $\Delta_{\text{del}} = \emptyset$, el driver no realiza llamadas de red ni escrituras en disco (cero impacto en CPU de router).

### 3.5 Flujo 5: Gateway Virtual Sidecar L3 (Split-Routing)
Para despliegues sin un router avanzado (UniFi o MikroTik), TeByPass proporciona un contenedor sidecar (`tebypass-gateway`):
- Basado en Alpine Linux ultra-ligero con soporte para `nftables`, `iproute2` y `wireguard`.
- El contenedor levanta una interfaz de túnel (`wg0`).
- Mediante políticas de enrutamiento basadas en reglas de `nftables` (conjuntos dinámicos `@tebypass_targets`), cualquier paquete entrante dirigido a las IPs catalogadas se enruta hacia `wg0` con enmascaramiento (`masquerade`).
- El tráfico no catalogado se reenvía directamente hacia la puerta de enlace predeterminada de la red local sin alteración alguna.
- Los dispositivos que requieran el bypass únicamente necesitan configurar la IP del sidecar como su puerta de enlace (Gateway) o añadir una ruta estática en su tabla local.

---

## 4. Ejemplos de Configuración Agnósticos (RFC 5737 / RFC 2606)

Toda la documentación y ejemplos del sistema utilizan rangos de red e identificadores conformes a los estándares RFC:

| Concepto | Ejemplo Válido en Documentación |
| :--- | :--- |
| **CIDR de Destino 1** | `192.0.2.0/24` (TEST-NET-1, RFC 5737) |
| **CIDR de Destino 2** | `198.51.100.0/24` (TEST-NET-2, RFC 5737) |
| **CIDR de Destino 3** | `203.0.113.0/24` (TEST-NET-3, RFC 5737) |
| **Subred Local Simulación** | `192.0.2.128/25` |
| **Dominio de Feed** | `https://feed.example.net/lists/bypass.json` |
| **Dominio de Streaming** | `sport-stream.example.org` |
| **Servicio de Telemetría** | `telemetry.example.org` |

---

## 5. Criterios de Aceptación del Sistema
1. **Rendimiento**: Resolución y cómputo de diferencias de 5,000 IPs en menos de 200 ms.
2. **Resiliencia**: Si el feed remoto no está disponible, el sistema mantiene el último estado válido almacenado en SQLite WAL sin degradar el tráfico local.
3. **Consumo de Recursos**: < 64 MB de memoria RAM en el proceso de Python y < 20 MB en el sidecar de gateway.
4. **Seguridad**: Autenticación mediante PIN maestro para cualquier mutación de estado a través de la API o la UI.
