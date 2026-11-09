# TeByPass — Hoja de Ruta (Roadmap)

Este documento detalla las fases de desarrollo de **TeByPass**, desglosadas en tareas atómicas con criterios de aceptación explícitos y seguimiento de estado.

---

## Resumen de Fases

- [x] **Fase 0: Scaffolding, Core Engine y Contratos Base** (Completada)
- [x] **Fase 1: Ingesta Dual y Parsers de Feeds Comunitarios** (Completada)
- [x] **Fase 2: Drivers de Router y Sidecar Gateway L3** (Completada)
- [x] **Fase 3: Web UI Industrial y Configuración 100% Web** (Completada)
- [x] **Fase 4: Empaquetado para Producción, Cifrado y Hardening** (Completada)

---

## Detalle de Fases y Tareas Atómicas

### Fase 0: Scaffolding, Core Engine y Contratos Base
*Objetivo: Establecer los cimientos del proyecto, arquitectura desacoplada, modelo de datos SQLite WAL, resolución DNS nativa, caché con ventana deslizante y suite de pruebas base.*

- [x] **Tarea 0.1: Estructura del Proyecto y Tooling**
  - Criterio de Aceptación: Repositorio inicializado, `pyproject.toml` con dependencias y herramientas (`ruff`, `mypy`, `pytest`), pipeline CI en GitHub Actions y `.gitignore` exhaustivo.
- [x] **Tarea 0.2: Documentación Maestra y Gobernanza**
  - Criterio de Aceptación: `AGENTS.md`, `PROJECT.md`, `ARCHITECTURE.md`, `DECISIONS.md` (ADR-001 a ADR-009) y `README.md` completos y conformes a reglas de privacidad y neutralidad RFC.
- [x] **Tarea 0.3: Contrato de Drivers de Red (`BaseRouterDriver`)**
  - Criterio de Aceptación: Interfaz abstracta con métodos `initialize`, `get_current_routes`, `sync_routes`, `health_check` y `close`. Implementación de `MockRouterDriver`.
- [x] **Tarea 0.4: Persistencia SQLite WAL**
  - Criterio de Aceptación: Configuración asíncrona de SQLAlchemy 2.0 y `aiosqlite` aplicando `PRAGMA journal_mode=WAL;`. Modelos para feeds, targets, IPs resueltas y eventos de sincronización.
- [x] **Tarea 0.5: Resolver DNS Nativo y Caché de Ventana Deslizante**
  - Criterio de Aceptación: Módulo `app.core.resolver` usando `getaddrinfo` del sistema; módulo `app.core.cache` con extensión dinámica de TTL; pruebas unitarias verificando la retención de rutas activas.
- [x] **Tarea 0.6: Motor de Reconciliación Idempotente**
  - Criterio de Aceptación: `app.core.engine` calcula diferencias atómicas ($\Delta_{\text{add}}$, $\Delta_{\text{del}}$) y despacha operaciones al driver seleccionado.

---

### Fase 1: Ingesta Dual y Parsers de Feeds Comunitarios
*Objetivo: Implementar la ingesta periódica y manual de destinos desde fuentes remotas y esquemas comunitarios.*

- [x] **Tarea 1.1: Parser de Feeds StopLiga / HayAHora**
  - Criterio de Aceptación: Módulo capaz de procesar respuestas JSON y texto plano, extrayendo FQDNs y prefijos CIDR válidos, con filtrado opcional por operador (ISP) y modo siempre-activo.
- [x] **Tarea 1.2: Cliente HTTP Asíncrono con Caché Condicional**
  - Criterio de Aceptación: Ingesta usando `httpx.AsyncClient` con soporte para encabezados `ETag` e `If-Modified-Since`, evitando descargas innecesarias y preservando rutas en caso de fallo remoto.
- [x] **Tarea 1.3: Endpoints REST para Gestión de Feeds y Targets**
  - Criterio de Aceptación: Endpoints de feeds, targets, carpetas/listas, importación masiva y toggle con esquemas Pydantic rigurosos.
- [x] **Tarea 1.4: Tarea en Segundo Plano Programada (Scheduler)**
  - Criterio de Aceptación: Ciclo periódico configurable que ejecuta la ingesta, resolución y reconciliación sin bloquear el bucle de eventos, con recarga en caliente del intervalo.

---

### Fase 2: Drivers de Router y Sidecar Gateway L3
*Objetivo: Integrar el motor con routers físicos y con el contenedor sidecar de enrutamiento.*

- [x] **Tarea 2.1: Gateway Sidecar (Alpine + nftables + WireGuard)**
  - Criterio de Aceptación: `gateway/Dockerfile` ligero (< 30 MB), script de entrada configurando reenvío IP, interfaz `wg0` leyendo configuración cargada desde la web y tablas de políticas.
- [x] **Tarea 2.2: Driver VirtualGateway (`app/drivers/virtual_gateway.py`)**
  - Criterio de Aceptación: Driver capaz de comunicarse con el sidecar de nftables para añadir/eliminar prefijos en el set dinámico.
- [x] **Tarea 2.3: Driver UniFi Network (`app/drivers/unifi.py`)**
  - Criterio de Aceptación: Integración con UniFi Network Application mediante API REST oficial para actualizar grupos de firewall de tipo dirección IP (IP Groups) vía API Key o usuario/contraseña.
- [x] **Tarea 2.4: Driver MikroTik RouterOS (`app/drivers/mikrotik.py`)**
  - Criterio de Aceptación: Integración con RouterOS v7 mediante la API REST (`/rest/ip/firewall/address-list`), soportando timeouts y reintentos exponenciales.

---

### Fase 3: Web UI Industrial y Configuración 100% Web
*Objetivo: Ofrecer un panel de control sobrio, reactivo y de alta telemetría en el puerto 9384, con toda la configuración editable desde la web.*

- [x] **Tarea 3.1: Plantillas Base Jinja2 con Identidad Visual Industrial**
  - Criterio de Aceptación: Paleta Carbón (`#0d0f12`), Grafito (`#16191f`), Ámbar (`#f59e0b`) y Verde (`#10b981`), tipografía técnica monoespaciada, cero dependencias de Node.js en tiempo de ejecución.
- [x] **Tarea 3.2: Componentes Reactivos con Alpine.js**
  - Criterio de Aceptación: Actualización en vivo del estado del router, número de rutas activas, tiempo desde la última sincronización y botón de sincronización manual forzada.
- [x] **Tarea 3.3: Gestión de Listas Personales y Destinos**
  - Criterio de Aceptación: Alta, edición, pausa, borrado e importación masiva de dominios/IPs, organizados por listas con colores distintivos.
- [x] **Tarea 3.4: Subida y Gestión de WireGuard desde la Web**
  - Criterio de Aceptación: Modal para pegar o cargar archivos `wg0.conf` con validación sintáctica de interfaz y claves, almacenado seguro en volumen de datos.

---

### Fase 4: Empaquetado para Producción, Cifrado y Hardening
*Objetivo: Empaquetar el sistema para despliegue productivo sencillo, seguro y monitorizable.*

- [x] **Tarea 4.1: Cifrado en Reposo de Secretos en SQLite (AES-128-CBC + HMAC-SHA256)**
  - Criterio de Aceptación: Credenciales de routers cifradas en disco con clave maestra restringida (`0o600`), enmascaradas en la API y auto-migración transparente de claves antiguas.
- [x] **Tarea 4.2: Detección Dinámica de Commit sin Binarios Externos**
  - Criterio de Aceptación: Lectura en Python puro de `.git/HEAD` desde montaje de sólo lectura en Docker, permitiendo actualizar versiones con un simple `git pull`.
- [x] **Tarea 4.3: Gobernanza de Licencia AGPLv3**
  - Criterio de Aceptación: Transición a GNU Affero General Public License v3 para asegurar la preservación comunitaria y sin ánimo de lucro del software.
- [x] **Tarea 4.4: Perfiles de Docker Compose de Producción**
  - Criterio de Aceptación: `docker-compose.yml` limpio sin secretos, variables bootstrap mínimas y perfil opcional para el gateway virtual.
