# TeByPass

Enrutamiento selectivo de destinos por una VPN o salida alternativa, con panel web,
API y configuración persistente. El resto del tráfico conserva su recorrido habitual.

TeByPass administra políticas de un router compatible o utiliza un gateway virtual
opcional. Admite destinos manuales (FQDN/CIDR) y fuentes de destinos, con sincronización,
pausa y estado de las rutas.

## Elegir el despliegue

| Entorno | Componentes necesarios |
|---|---|
| UniFi, Omada, OPNsense o MikroTik con salida VPN preparada | Aplicación TeByPass; el router enruta el tráfico |
| Router sin integración compatible | Aplicación + gateway virtual WireGuard en un host Linux conectado a la LAN |
| Evaluación sin modificar la red | Aplicación con driver Mock |

**El gateway virtual es opcional.** Los drivers de router no necesitan el servicio
`tebypass-gateway`, macvlan ni cambiar la puerta de enlace de los clientes.
La compatibilidad depende de las capacidades y configuración del equipo;
[consulta los requisitos](docs/INSTALLATION.md#requisitos-por-driver).

## Instalación

Requiere Docker Engine y Docker Compose v2. La
[guía de instalación](docs/INSTALLATION.md) explica la elección de driver, el acceso,
la configuración y la comprobación desde un cliente.

Mientras no haya una release pública, se puede compilar desde el código:

```sh
git clone https://github.com/TSCNEO/tebypass.git
cd tebypass
TEBYPASS_GIT_COMMIT=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait tebypass
```

Abre `http://<servidor>:9384` y consulta localmente la clave de acceso:

```sh
docker compose exec tebypass cat /data/admin_token
```

En **Ajustes**, selecciona el driver, configura la salida y prueba la conexión.
La fuente inicial está pausada; actívala después de comprobar la configuración.
Para las siguientes operaciones de una instalación compilada se deben conservar ambos
archivos Compose. [Actualización y copias](docs/OPERATIONS.md).

## Instalación con un LLM

Copia este encargo en un asistente con acceso al servidor o capacidad para guiar los pasos:

> Quiero desplegar TeByPass. Lee README.md y docs/LLM_DEPLOYMENT.md del repositorio
> https://github.com/TSCNEO/tebypass. Sigue su entrevista por etapas: pregunta primero
> qué router tengo, dónde se ejecutará y si ya existe una VPN. Elige conmigo el driver
> antes de desplegar. Usa solo los componentes necesarios, conserva cualquier instalación
> existente y verifica el tráfico desde un cliente. No pidas secretos en el chat.

[Protocolo completo para el asistente](docs/LLM_DEPLOYMENT.md).

## Alcance

- Una política de salida y un driver por instalación; las carpetas organizan destinos.
- UniFi usa Traffic Routes V2. Omada y OPNsense usan adaptadores de stopliga.
  Las pruebas de contrato no equivalen a certificación en todos los equipos o firmwares.
- Gateway virtual y MikroTik: IPv4. El sidecar comparte host y volumen con la aplicación.
- TeByPass no suministra un proveedor VPN ni configura automáticamente el túnel del router.
- El estado saludable de Docker no acredita conectividad de los clientes por la VPN.

## Documentación

- [Instalación y requisitos](docs/INSTALLATION.md)
- [Operación, diagnóstico, copias y actualización](docs/OPERATIONS.md)
- [Desarrollo y comprobaciones](CONTRIBUTING.md)
- [Índice técnico e histórico](docs/README.md)

## Licencia y créditos

AGPL-3.0-or-later. Gracias a [jcastro/stopliga](https://github.com/jcastro/stopliga)
y a la comunidad [hayahora.futbol](https://hayahora.futbol).
[Dependencias y atribuciones](THIRD_PARTY_NOTICES.md).
