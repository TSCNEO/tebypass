# AGENTS.md — Directrices Maestras de TeByPass

Este documento establece las normas operativas, arquitectónicas, de seguridad y de calidad para cualquier agente de IA o desarrollador humano que trabaje en el repositorio **TeByPass**.

---

## 1. Misión y Filosofía del Proyecto

**TeByPass** es un motor agnóstico, modular y de alto rendimiento para el desvío dinámico de tráfico en Capa 3 (L3 split-routing) hacia gateways o túneles de salida específicos para destinos de red arbitrarios (FQDNs y CIDRs).

### Regla Fundamental: Cero Monolitos ("0 Monolitos")
El proyecto prohíbe de forma estricta la arquitectura monolítica. El sistema debe mantenerse desacoplado en componentes independientes con interfaces y contratos formales:
1. **Core Engine (`app/core`)**: Orquestación pura, algoritmos de reconciliación idempotente, resolución DNS asíncrona y caché de ventana deslizante (sliding-window TTL). No tiene conocimiento de routers ni de protocolos de red específicos.
2. **Drivers de Router (`app/drivers`)**: Módulos intercambiables que implementan la interfaz abstracta `BaseRouterDriver`. Cada driver traduce los CIDRs objetivo al plano de control del router o firewall correspondiente (UniFi, MikroTik, Linux nftables, Mock). Ningún driver accede directamente a la base de datos ni a la API HTTP.
3. **API y Web (`app/api`, `app/templates`)**: Interfaz REST ligera con FastAPI y UI renderizada en servidor con Jinja2 + Alpine.js. Consume exclusivamente los servicios del Core Engine.
4. **Virtual Gateway Sidecar (`gateway/`)**: Contenedor de red independiente (Alpine Linux + nftables + WireGuard) desacoplado del runtime de Python. Puede ejecutarse en el mismo host o en un nodo de red separado.

---

## 2. Restricciones Críticas de Privacidad y Agnosticismo Radical

Estas reglas tienen **prioridad máxima e incondicional**. Su violación compromete la seguridad y privacidad del proyecto.

### 2.1 Cero Fugas de Infraestructura Privada
- **PROHIBIDO** incluir cualquier dirección IP o prefijo perteneciente a redes privadas reales o entornos de producción/desarrollo.
- **PROHIBIDO** incluir nombres de hosts, servidores, nodos, hipervisores o identificadores de hardware reales.
- **PROHIBIDO** incluir dominios, zonas DNS o credenciales de entornos reales, ni siquiera a modo de advertencia o contraejemplo.
- **OBLIGATORIO** utilizar exclusivamente rangos de documentación oficiales RFC (RFC 5737 / RFC 3849 / RFC 2606) para cualquier ejemplo, test o documentación.

### 2.2 Prohibición de Nombres de Servicios Bloqueados Reales
- **PROHIBIDO** incluir nombres o marcas de plataformas bloqueadas o listas de censura reales (como servicios de streaming ilegal específicos, etc.).
- Utilizar **siempre y exclusivamente** rangos reservados para documentación (RFC 5737) y nombres ficticios neutrales (RFC 2606):
  - Direcciones IPv4 de ejemplo:
    - `192.0.2.0/24` (TEST-NET-1)
    - `198.51.100.0/24` (TEST-NET-2)
    - `203.0.113.0/24` (TEST-NET-3)
  - Direcciones IPv6 de ejemplo:
    - `2001:db8::/32` (RFC 3849)
  - Dominios y FQDNs de ejemplo:
    - `example.com`
    - `sport-stream.example.org`
    - `feed.example.net`
    - `edge-node-01.example.net`

### 2.3 100% Agnóstico a la Red
El software no asume topologías de red fijas. Las variables base de arranque (`TEBYPASS_PORT`, `TEBYPASS_DATABASE_URL`) se inyectan como bootstrap mínimo, y toda la configuración operativa (routers, credenciales cifradas en reposo, intervalos de sincronización, feeds e IPs) se gestiona de forma reactiva y persistente directamente desde la interfaz web.

---

## 3. Procedimiento Obligatorio de Trabajo

Cada tarea o modificación en el código debe seguir de forma estricta el ciclo de 5 fases:

```
[1. ANALIZAR] ──> [2. PLANIFICAR] ──> [3. IMPLEMENTAR] ──> [4. VERIFICAR] ──> [5. DOCUMENTAR]
```

1. **ANALIZAR**:
   - Inspeccionar el código existente y los contratos de interfaz antes de escribir una sola línea.
   - Identificar dependencias e impactos en el desacoplamiento.
2. **PLANIFICAR**:
   - Definir cambios atómicos por módulo.
   - Asegurar que no se rompe la regla de "0 monolitos".
3. **IMPLEMENTAR**:
   - Código limpio, tipado estricto y comentarios técnicos concisos.
   - Separación estricta de responsabilidades.
4. **VERIFICAR**:
   - Ejecutar suite de pruebas: `pytest`.
   - Ejecutar linter: `ruff check .` y formateador `ruff format --check .`.
   - Ejecutar verificación de tipos: `mypy app tests`.
   - Auditar que no existen credenciales ni IPs reales en los cambios.
5. **DOCUMENTAR**:
   - Actualizar documentación en `docs/` si se modifica la arquitectura, API o modelos.
   - Reflejar decisiones técnicas no triviales en `docs/DECISIONS.md` (ADR).

---

## 4. Estándares Técnicos y Calidad de Código

### 4.1 Stack Tecnológico
- **Lenguaje**: Python >= 3.12 (aprovechando mejoras de tipos, `type` statement, optimizaciones del runtime).
- **Web Framework**: FastAPI (asíncrono, OpenAPI nativo).
- **Base de Datos**: SQLite 3 en modo **WAL** (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`), gestionado asíncronamente con SQLAlchemy 2.0 y `aiosqlite`.
- **Frontend**: Jinja2 + Tailwind CSS empaquetado + Alpine.js.
  - **Cero Node.js en runtime**: No se permite introducir herramientas Node/NPM en los contenedores de ejecución ni en la imagen base.
- **Sidecar de Red**: Contenedor Alpine Linux minimalista con `nftables`, `iproute2` y `wireguard-tools`.
- **Puerto por Defecto**: `9384`.

### 4.2 Criterios de Calidad
- **Ruff**: Reglas habilitadas: `E`, `W`, `F`, `I`, `B`, `C4`, `UP`, `SIM`. Tolerancia cero a errores de linter.
- **Mypy**: Modo estricto (`strict = true`). Todas las funciones, métodos y argumentos deben tener type hints explícitos.
- **Pytest**: Pruebas unitarias y de integración para cada driver y componente del core. Las pruebas no deben requerir hardware físico real (utilizar `MockRouterDriver` y mocks asíncronos para DNS y llamadas HTTP).

---

## 5. Convenciones de Control de Versiones (Git)

### 5.1 Commits Semánticos
Los mensajes de commit deben seguir el estándar Conventional Commits:
- `feat: <descripción concisa en minúsculas>`: Nueva funcionalidad.
- `fix: <descripción>`: Corrección de un fallo.
- `refactor: <descripción>`: Cambio de código que no altera funcionalidad ni arregla bugs.
- `docs: <descripción>`: Modificaciones exclusivas en documentación.
- `test: <descripción>`: Añadido o corrección de pruebas.
- `chore: <descripción>`: Mantenimiento de herramientas, CI o dependencias.

### 5.2 Git Flow
- La rama principal es `main`.
- Toda nueva característica o refactorización de gran envergadura se desarrolla en ramas descriptivas (`feat/...`, `fix/...`) antes de integrarse en `main`.
- Nunca realizar `push --force` en ramas compartidas.
- Verificar el estado real en el servidor remoto tras cada push (`git ls-remote`).

---

## 6. Identidad Visual y Guía de Estilo de la UI

La interfaz web de TeByPass sigue una estética **industrial / NOC / telemetría**, sobria y técnica. Se prohíbe terminantemente el uso de gradientes violeta/azul genéricos ("AI slop").

### Paleta Cromática
- **Fondo Primario (Carbón)**: `#0d0f12` (superficie base, terminal, canvas).
- **Fondo Secundario (Grafito)**: `#16191f` (tarjetas, tablas, paneles de telemetría).
- **Bordes y Divisores**: `#262b35` (líneas sutiles de alta definición).
- **Color de Acento Primario (Ámbar Fósforo)**: `#f59e0b` (indicadores activos, botones principales, alertas preventivas).
- **Color de Telemetría (Verde Tráfico)**: `#10b981` (rutas activas, sincronización correcta, ping OK).
- **Color de Advertencia / Error (Rojo Alarma)**: `#ef4444` (rutas degradadas, fallo de driver, gateway inalcanzable).
- **Tipografía**: Monospace para datos de red, IPs, tiempos y estadísticas (`JetBrains Mono`, `Fira Code`, o `ui-monospace`); Sans-serif limpia y técnica para etiquetas (`Inter` o `system-ui`).

---

## 7. Protocolo para Agentes LLM: Despliegue del Virtual Gateway

Cuando un agente de IA asista a un usuario en el despliegue del modo **Gateway Virtual Propio** (`tebypass-gateway`), debe seguir este flujo interactivo estructurado, sin omitir pasos ni asumir parámetros a ciegas:

### 7.1 Fase 1: Descubrimiento y Topología de Red
1. **Identificar la interfaz padre del host**:
   - Inspeccionar interfaces de red disponibles (`ip -br link` o `ip -br addr`).
   - Identificar cuál es la interfaz conectada a la LAN doméstica física (habitualmente `eth0`, `enp3s0` o un bridge `vmbr0` en Proxmox).
2. **Identificar subred y puerta de enlace**:
   - Detectar la subred local (ej. `192.168.1.0/24`) y la IP del router principal (ej. `192.168.1.1`).

### 7.2 Fase 2: Selección y Escaneo de IP Libre
1. **Buscar una dirección IP disponible**:
   - Preguntar al usuario qué IP desea utilizar O proponerle escanear la red local en busca de una IP desocupada mediante barrido ICMP/ARP rápido:
     ```bash
     for ip in $(seq 20 35); do ping -c 1 -W 1 192.168.1.$ip >/dev/null 2>&1 || echo "192.168.1.$ip LIBRE"; done
     ```
   - Recomendar siempre una dirección fuera del rango dinámico del pool DHCP del router para evitar solapamientos.

### 7.3 Fase 3: Recordatorio y Reserva DHCP Obligatoria
1. **Recordar al usuario la reserva en el router**:
   - **Instrucción crítica**: El agente DEBE indicar al usuario explícitamente:
     > *"He asignado la IP X.X.X.X al Gateway Virtual. Por favor, crea una reserva fija en el servidor DHCP de tu router (UniFi, MikroTik, Keenetic, router de operadora, etc.) asociada a la dirección MAC configurada, y ponle una nota como 'TeByPass-Gateway' para no olvidarte."*
2. **Fijar dirección MAC determinista**:
   - En `docker-compose.yml`, configurar `mac_address` (o vía `TEBYPASS_GATEWAY_MAC` en `.env`) para que Docker no genere una MAC aleatoria en cada recreación del contenedor, asegurando que la reserva DHCP del router nunca expire ni quede huérfana.

### 7.4 Fase 4: Creación de Red y Despliegue
1. **Crear la red macvlan de Docker**:
   ```bash
   docker network create -d macvlan \
     --subnet="192.168.1.0/24" \
     --gateway="192.168.1.1" \
     -o parent="eth0" \
     tebypass-lan
   ```
2. **Configurar `.env`**:
   ```bash
   TEBYPASS_GATEWAY_IP=192.168.1.26
   TEBYPASS_GATEWAY_MAC=02:42:c0:a8:01:1a
   TEBYPASS_LAN_NETWORK=tebypass-lan
   ```
3. **Subir WireGuard**:
   - Subir el archivo `wg0.conf` a través de la interfaz web (`/settings` o modal WireGuard) o depositarlo en `/data/wireguard/wg0.conf`.
4. **Levantar el servicio**:
   ```bash
   docker compose --profile gateway up -d --build
   ```

### 7.5 Fase 5: Verificación y Guía de Prueba para Clientes
1. **Comprobar conectividad y socket**:
   - Verificar ping hacia la IP del gateway desde otro equipo de la LAN.
   - Verificar salud vía API: `GET /api/v1/status` (driver `virtual_gateway` con estado `healthy` y transporte `unix_socket`).
2. **Instrucciones para el usuario**:
   - **En Smart TV / Deco**: Indicar cómo configurar IP manual en la TV poniendo como Puerta de Enlace la IP del Virtual Gateway y DNS el habitual.
   - **En Mac / Linux**: Proporcionar el comando de ruta temporal para validar sin afectar a otros dispositivos:
     ```bash
     sudo route add -net <CIDR_OBJETIVO> <IP_GATEWAY>
     ```

---

## 8. Agradecimientos y Créditos

TeByPass reconoce y agradece formalmente el trabajo y la investigación comunitaria de:
- **jcastro / stopliga**: Por el esfuerzo pionero en catalogación y transparencia de bloqueos arbitrarios.
- **Comunidad hayahora.futbol**: Por mantener fuentes actualizadas y abiertas para la comunidad técnica.
