# TeByPass

<p align="center">
  <strong>Motor agnóstico y modular de desvío dinámico en Capa 3 (L3 Split-Routing)</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License MIT">
  <img src="https://img.shields.io/badge/code%20style-ruff-000000.svg" alt="Ruff">
  <img src="https://img.shields.io/badge/types-mypy%20strict-informational.svg" alt="Mypy Strict">
  <img src="https://img.shields.io/badge/architecture-0%20monolitos-amber.svg" alt="0 Monoliths">
</p>

---

## 🎯 ¿Qué es TeByPass?

**TeByPass** es un motor de enrutamiento selectivo de alta eficiencia diseñado para evadir bloqueos arbitrarios en redes sin la penalización de un túnel VPN completo (Full-Tunnel) ni la sobrecarga de descifrado TLS de los proxies de Capa 7.

El sistema monitoriza listas dinámicas de destinos (FQDNs y prefijos CIDR), resuelve periódicamente los nombres utilizando el resolver nativo del anfitrión, mantiene una caché de ventana deslizante para evitar caídas durante transmisiones activas, e inyecta únicamente los prefijos necesarios en el router o sidecar de la red local.

---

## ⚡ Características Principales

- **0 Monolitos**: Arquitectura estrictamente modular y desacoplada en cuatro capas (Core Engine, Drivers de Router, Web/API, Sidecar Gateway).
- **L3 Split-Routing Puro**: El tráfico ordinario no catalogado navega directamente a velocidad nativa sin tocar ningún túnel. Solo los destinos catalogados se desvían por WireGuard.
- **Resolución DNS Nativa**: Delegación en la pila `libc` del sistema (`getaddrinfo`), respetando servidores DNS locales (AdGuard Home, Pi-hole, Unbound) sin servidores DNS fijos forzados.
- **Caché con Ventana Deslizante (Sliding-Window TTL)**: Evita microcortes o expiraciones abruptas de IPs en mitad de eventos de streaming.
- **Reconciliación Idempotente**: Cálculo diferencial estricto antes de aplicar cambios en el router, evitando escrituras repetidas y saturación de tablas.
- **Drivers Pluggables**:
  - `MockRouterDriver`: Para pruebas automatizadas y desarrollo sin hardware.
  - `VirtualGatewayDriver`: Para integración directa con el contenedor sidecar Alpine + nftables.
  - `UniFiRouterDriver`: Para routers UniFi (Dream Machine / Cloud Gateway).
  - `MikroTikRouterDriver`: Para RouterOS v7 mediante REST API.
- **UI Industrial sin Node.js**: Panel de telemetría y control basado en Jinja2 + Alpine.js en puerto por defecto `9384`.

---

## 🏗️ Resumen de Arquitectura

```
[Dispositivos LAN] ───▶ [Router / TeByPass Sidecar]
                               │
            ┌──────────────────┴──────────────────┐
     (Destino Ordinario)                   (Destino Catalogado)
            │                                     │
            ▼                                     ▼
    [Salida ISP Directa]                  [Túnel WireGuard L3]
    (Baja Latencia / 100% Ancho)          (Tráfico Evasión Seguro)
```

---

## 🚀 Inicio Rápido (Quickstart)

### Requisitos Previos
- Docker y Docker Compose (o Python 3.12+ para ejecución en host).

### Despliegue con Docker Compose
1. Clona el repositorio e inicia los servicios:
```bash
docker compose up -d
```
2. Accede al panel de control en tu navegador:
```
http://localhost:9384
```

### Configuración con Variables de Entorno
Crea un archivo `.env` o configura las variables en tu orquestador:

```bash
# Puerto de escucha del servicio web
TEBYPASS_PORT=9384
TEBYPASS_HOST=0.0.0.0

# Base de datos SQLite (modo WAL automático)
TEBYPASS_DB_PATH=/data/tebypass.db

# Driver de enrutamiento activo: mock | virtual_gateway | unifi | mikrotik
TEBYPASS_ROUTER_DRIVER=mock

# Intervalo de reconciliación en segundos
TEBYPASS_SYNC_INTERVAL_SECONDS=300

# Ventana deslizante de TTL para caché (segundos)
TEBYPASS_SLIDING_WINDOW_SECONDS=3600

# PIN Maestro para operaciones privilegiadas
TEBYPASS_MASTER_PIN_HASH=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

# Feeds remotos iniciales (ejemplo neutral conforme a RFC 2606)
TEBYPASS_INITIAL_FEEDS='[{"name":"Neutral Feed","url":"https://feed.example.net/bypass.json","format":"json"}]'
```

---

## 🧪 Desarrollo Local y Verificación

Instalación de dependencias con `uv` o `pip`:
```bash
# Con uv (recomendado)
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Ejecución de linters y formateadores
ruff check .
ruff format --check .

# Verificación de tipos estricta
mypy app tests

# Ejecución de la suite de pruebas
pytest
```

---

## 🔒 Privacidad y Agnosticismo

Este proyecto es **100% agnóstico a cualquier infraestructura de red**. No incluye credenciales predeterminadas, nombres de servidores de prueba reales ni direcciones IP de infraestructuras personales. Todos los ejemplos de documentación emplean bloques reservados por IANA/IETF según **RFC 5737** (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) y **RFC 2606** (`example.com`, `example.org`, `example.net`).

---

## 🙏 Agradecimientos y Créditos

TeByPass reconoce y agradece profundamente a:
- **jcastro / stopliga**: Por el trabajo de documentación, transparencia y catalogación técnica contra el bloqueo arbitrario.
- **Comunidad hayahora.futbol**: Por mantener fuentes abiertas de conocimiento y coordinación comunitaria.

---

## 📄 Licencia

Este proyecto está bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más información.
