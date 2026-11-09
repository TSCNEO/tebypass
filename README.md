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

## Instalación con un LLM

Para que un asistente lo monte, copia y pega este encargo en tu LLM. Si puede ejecutar
comandos en el servidor, podrá realizar el despliegue; si no, te guiará paso a paso:

> Quiero que instales y configures TeByPass en mi entorno. Lee README.md y
> docs/LLM_DEPLOYMENT.md del repositorio https://github.com/TSCNEO/tebypass.
> Antes de ejecutar nada, pregúntame qué router y modelo tengo, dónde quiero instalarlo,
> si tengo Docker y una VPN preparada, qué dispositivos quiero usar y cómo accederé al panel.
> Sigue la entrevista por etapas y elige conmigo el driver. Instala solo los componentes
> necesarios: con un router compatible y salida preparada no levantes el gateway virtual.
> Conserva cualquier instalación existente, configura la salida y comprueba el tráfico
> desde un cliente. No pidas secretos en el chat; indícame dónde introducirlos de forma
> segura. Al terminar, explica cómo acceder, actualizar, hacer copias y revertir los cambios.

[Protocolo completo para el asistente](docs/LLM_DEPLOYMENT.md).

## Instalación manual rápida

1. **Elige el driver** con la tabla anterior y revisa sus
   [requisitos](docs/INSTALLATION.md#requisitos-por-driver). Para un router compatible,
   prepara su VPN/salida y el acceso API. Para gateway virtual, completa primero la
   [preparación LAN](docs/OPERATIONS.md#gateway-virtual-despliegue-en-la-lan).
2. **Prepara el servidor:** instala Docker Engine, Docker Compose v2 y Git para compilar.
   Los comandos siguientes arrancan solo la aplicación; la
   [guía de instalación](docs/INSTALLATION.md) incluye el arranque específico del gateway
   y la alternativa con imágenes de release cuando estén publicadas.
3. **Descarga y arranca** desde el código:

```sh
git clone https://github.com/TSCNEO/tebypass.git
cd tebypass
TEBYPASS_GIT_COMMIT=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait tebypass
```

4. **Accede:** abre `http://<servidor>:9384`, consulta la clave localmente con el comando
   siguiente e introdúcela en la pantalla de acceso. No la compartas.

```sh
docker compose exec tebypass cat /data/admin_token
```

5. **Configura:** en **Ajustes**, selecciona el driver, completa sus campos, elige la
   salida y pulsa **Probar conexión**. Con gateway virtual, arranca también su perfil
   según la guía y sube WireGuard desde el panel.
6. **Comprueba:** añade un destino de prueba controlado, sincroniza y verifica desde un
   cliente que usa la salida elegida. Comprueba también que el tráfico no seleccionado
   mantiene su salida habitual y que pausar retira el desvío.
7. **Activa las fuentes deseadas:** la fuente inicial está pausada. Actívala después de
   validar la salida y prepara una [copia de seguridad](docs/OPERATIONS.md#acceso-y-secretos).

Para las siguientes operaciones de una instalación compilada se deben conservar ambos
archivos Compose. Consulta [actualización y copias](docs/OPERATIONS.md).

## Ejemplo: una TV o PC con gateway virtual

Después de [preparar y arrancar el gateway](docs/INSTALLATION.md#5-solo-si-se-ha-elegido-gateway-virtual),
selecciona `virtual_gateway` en Ajustes, sube WireGuard y comprueba su estado.
**En cada dispositivo que vaya a utilizarlo, cambia la puerta de enlace por la IP LAN
del gateway virtual**, que puede ser distinta de la IP del servidor del panel.

Ejemplo ficticio, solo de documentación; sustituye los valores por los de tu LAN:

| Ajuste del dispositivo | Antes | Con gateway virtual |
|---|---|---|
| IP de la TV/PC | `192.0.2.50` | `192.0.2.50` |
| Máscara | `255.255.255.0` | `255.255.255.0` |
| Puerta de enlace | `192.0.2.1` (router) | `192.0.2.26` (gateway virtual) |
| DNS | `192.0.2.1` | `192.0.2.1` (DNS habitual) |

En la TV suele estar en **Red → Configuración IP → Manual**; los nombres varían por
fabricante. Guarda primero los ajustes anteriores. Si pasas de DHCP a manual, utiliza
una IP reservada o excluida del pool para evitar duplicados. El gateway debe ser accesible
desde la misma LAN. **No pongas su IP como DNS:** el sidecar no ofrece servidor DNS.

El recorrido será dispositivo → gateway virtual → VPN para destinos seleccionados;
el resto saldrá por el router habitual. Arrancar Docker por sí solo no cambia el
recorrido de los dispositivos. Prueba primero uno y verifica ambos tipos de tráfico.
Para revertir, restaura la puerta de enlace anterior o el modo DHCP si era el original.
Con drivers de router compatibles no hace falta este cambio en los clientes.

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
