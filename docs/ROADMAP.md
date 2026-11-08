# TeByPass — Hoja de Ruta (Roadmap)

Este documento detalla las fases de desarrollo de **TeByPass**, desglosadas en tareas atómicas con criterios de aceptación explícitos y seguimiento de estado.

---

## Resumen de Fases

- [x] **Fase 0: Scaffolding, Core Engine y Contratos Base** (Completada en inicialización)
- [ ] **Fase 1: Ingesta Dual y Parsers de Feeds Comunitarios**
- [ ] **Fase 2: Drivers de Router y Sidecar Gateway L3**
- [ ] **Fase 3: Web UI Industrial y Telemetría en Tiempo Real**
- [ ] **Fase 4: Empaquetado para Producción y Hardening**

---

## Detalle de Fases y Tareas Atómicas

### Fase 0: Scaffolding, Core Engine y Contratos Base
*Objetivo: Establecer los cimientos del proyecto, arquitectura desacoplada, modelo de datos SQLite WAL, resolución DNS nativa, caché con ventana deslizante y suite de pruebas base.*

- [x] **Tarea 0.1: Estructura del Proyecto y Tooling**
  - Criterio de Aceptación: Repositorio inicializado, `pyproject.toml` con dependencias y herramientas (`ruff`, `mypy`, `pytest`), pipeline CI en GitHub Actions y `.gitignore` exhaustivo.
- [x] **Tarea 0.2: Documentación Maestra y Gobernanza**
  - Criterio de Aceptación: `AGENTS.md`, `PROJECT.md`, `ARCHITECTURE.md`, `DECISIONS.md` (ADR-001 a ADR-006) y `README.md` completos y conformes a reglas de privacidad y neutralidad RFC.
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

- [ ] **Tarea 1.1: Parser de Feeds StopLiga / HayAHora**
  - Criterio de Aceptación: Módulo capaz de procesar respuestas JSON y texto plano, extrayendo FQDNs y prefijos CIDR válidos, descartando comentarios o líneas malformadas.
- [ ] **Tarea 1.2: Cliente HTTP Asíncrono con Caché Condicional**
  - Criterio de Aceptación: Ingesta usando `httpx.AsyncClient` con soporte para encabezados `ETag` y `If-Modified-Since`, evitando descargas innecesarias.
- [ ] **Tarea 1.3: Endpoints REST para Gestión de Feeds y Targets**
  - Criterio de Aceptación: `POST /api/v1/feeds`, `GET /api/v1/feeds`, `POST /api/v1/targets` y `DELETE /api/v1/targets/{id}` con validación estricta de esquemas Pydantic.
- [ ] **Tarea 1.4: Tarea en Segundo Plano Programada (Scheduler)**
  - Criterio de Aceptación: Ciclo periódico configurable (`TEBYPASS_SYNC_INTERVAL_SECONDS`) que ejecuta la ingesta, resolución y reconciliación sin bloquear el bucle de eventos.

---

### Fase 2: Drivers de Router y Sidecar Gateway L3
*Objetivo: Integrar el motor con routers físicos y con el contenedor sidecar de enrutamiento.*

- [ ] **Tarea 2.1: Gateway Sidecar (Alpine + nftables + WireGuard)**
  - Criterio de Aceptación: `gateway/Dockerfile` ligero (< 30 MB), script de entrada configurando reenvío IP, interfaz `wg0` y conjuntos dinámicos `@tebypass_targets`.
- [ ] **Tarea 2.2: Driver VirtualGateway (`app/drivers/virtual_gateway.py`)**
  - Criterio de Aceptación: Driver capaz de comunicarse con el sidecar (vía comandos CLI o socket de control IPC) para añadir/eliminar prefijos en el set de nftables.
- [ ] **Tarea 2.3: Driver UniFi Network (`app/drivers/unifi.py`)**
  - Criterio de Aceptación: Integración con UniFi Network Application mediante API REST oficial para actualizar grupos de firewall de tipo dirección IP (IP Groups).
- [ ] **Tarea 2.4: Driver MikroTik RouterOS (`app/drivers/mikrotik.py`)**
  - Criterio de Aceptación: Integración con RouterOS v7 mediante la API REST (`/rest/ip/firewall/address-list`), soportando timeouts y reintentos exponenciales.

---

### Fase 3: Web UI Industrial y Telemetría en Tiempo Real
*Objetivo: Ofrecer un panel de control sobrio, reactivo y de alta telemetría en el puerto 9384.*

- [ ] **Tarea 3.1: Plantillas Base Jinja2 con Identidad Visual Industrial**
  - Criterio de Aceptación: Paleta Carbón (`#0d0f12`), Grafito (`#16191f`), Ámbar (`#f59e0b`) y Verde (`#10b981`), tipografía técnica monoespaciada, cero dependencias de Node.js en tiempo de ejecución.
- [ ] **Tarea 3.2: Componentes Reactivos con Alpine.js**
  - Criterio de Aceptación: Actualización en vivo del estado del enrutador, número de rutas activas, tiempo desde la última sincronización y botón de sincronización manual forzada.
- [ ] **Tarea 3.3: Vista de Inspección de Rutas Activas**
  - Criterio de Aceptación: Tabla paginada y filtrable de prefijos CIDR inyectados, origen (feed o manual), IP resuelta y tiempo restante de la ventana deslizante.
- [ ] **Tarea 3.4: Seguridad y Autenticación con PIN Maestro**
  - Criterio de Aceptación: Modal de desbloqueo mediante PIN para acciones administrativas, emisión de cookies firmadas HMAC-SHA256 y protección CSRF.

---

### Fase 4: Empaquetado para Producción y Hardening
*Objetivo: Empaquetar el sistema para despliegue productivo sencillo, seguro y monitorizable.*

- [ ] **Tarea 4.1: Perfiles de Docker Compose de Producción**
  - Criterio de Aceptación: `docker-compose.yml` con perfiles (`standalone`, `with-gateway`), healthchecks nativos y montajes de volúmenes persistentes.
- [ ] **Tarea 4.2: Cobertura de Pruebas > 90%**
  - Criterio de Aceptación: Cobertura de tests unitarios y de integración que supere el 90% en `app/core` y `app/drivers`.
- [ ] **Tarea 4.3: Documentación de Operaciones y Guías de Red**
  - Criterio de Aceptación: Guías paso a paso para configurar clientes (Smart TVs, consolas, PCs) y topologías de enrutamiento estático en routers domésticos.
