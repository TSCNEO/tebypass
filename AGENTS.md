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
El software no asume topologías de red fijas. Toda dirección IP, interfaz, subred y credencial se inyecta mediante variables de entorno configurables con prefijo `TEBYPASS_`.

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

## 7. Agradecimientos y Créditos

TeByPass reconoce y agradece formalmente el trabajo y la investigación comunitaria de:
- **jcastro / stopliga**: Por el esfuerzo pionero en catalogación y transparencia de bloqueos arbitrarios.
- **Comunidad hayahora.futbol**: Por mantener fuentes actualizadas y abiertas para la comunidad técnica.
