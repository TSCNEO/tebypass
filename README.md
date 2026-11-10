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

## Cómo funciona con un router compatible

TeByPass se conecta a la API del router/controlador y mantiene los destinos de la
política de salida. **El router transporta el tráfico y aloja la VPN; el contenedor
TeByPass solo administra la política.** El servidor del panel necesita acceso a esa
API, pero no tiene que estar en el recorrido de los paquetes.

Los dispositivos conservan su puerta de enlace y DNS habituales. Cuando acceden a un
destino seleccionado, el router lo envía por la VPN/salida configurada; el resto sigue
su recorrido habitual. No hace falta instalar nada en la TV/PC, subir WireGuard al
panel ni arrancar `tebypass-gateway`. El alcance sobre clientes depende de la política
del driver y del router: no es una selección individual de dispositivos desde TeByPass.

### Ejemplo con UniFi

Supongamos un gateway UniFi con Traffic Routes V2, un cliente VPN ya operativo llamado
`VPN-Salida` y una TV que utiliza ese gateway como router habitual:

1. Arranca solo `tebypass` con los pasos de instalación manual anteriores.
2. En **Ajustes**, selecciona **UniFi**, introduce la dirección del controlador, sitio
   y API key local, selecciona `VPN-Salida` y pulsa **Probar conexión**.
3. Añade un destino manual habilitado y sincroniza. Por ejemplo, `198.51.100.80/32`
   ilustra la sintaxis de una IP concreta; es un rango reservado de documentación y
   debe sustituirse por un destino real bajo tu control para probar conectividad.
4. Comprueba en UniFi que la Traffic Route gestionada contiene el destino y apunta
   al cliente VPN elegido. Abre una conexión nueva hacia ese destino desde la TV/PC
   y verifica la salida mediante registros/contadores del router o del servidor de prueba.
5. Comprueba otro destino no seleccionado: debe conservar la salida habitual.
   Prueba la pausa global y verifica que se desactiva la política gestionada.

La TV sigue usando el router UniFi como puerta de enlace durante toda la prueba.
La política UniFi actual abarca todos los clientes alcanzados por esa regla, no solo
la TV. Si se usa Omada, OPNsense o MikroTik, el recorrido es equivalente, pero cambian
los [preparativos y campos del driver](docs/OPERATIONS.md#preparación-por-router).
La respuesta ante caída de la VPN depende del router y de su política; el fail-closed
del gateway virtual no configura automáticamente esa protección en otros drivers.

## Ejemplo: una TV o PC con gateway virtual

Después de [preparar y arrancar el gateway](docs/INSTALLATION.md#5-solo-si-se-ha-elegido-gateway-virtual),
selecciona `virtual_gateway` en Ajustes, sube WireGuard y comprueba su estado.
**En cada dispositivo que vaya a utilizarlo, cambia la puerta de enlace por la IP LAN
del gateway virtual**, que puede ser distinta de la IP del servidor del panel.

### Cargar la configuración WireGuard

1. Obtén de tu proveedor VPN o administrador un archivo de cliente WireGuard (`.conf`).
   TeByPass no genera la suscripción ni las credenciales de la VPN.
2. Con el sidecar arrancado, selecciona **Gateway virtual propio** en **Ajustes** y pulsa
   **Guardar ajustes** antes de cargar el archivo.
3. En **Ajustes → WireGuard → Subir o pegar tu wg0.conf**, selecciona el archivo
   (también puedes pegar su contenido en el campo del panel).
4. Pulsa **Guardar configuración**. Seleccionar el archivo por sí solo no lo sube.
   Con el driver virtual seleccionado, el servicio guarda y solicita aplicar el túnel;
   comprueba el aviso **Túnel aplicado** y la salud del driver.
5. Si aparece un error 502, el archivo puede estar guardado pero el túnel no aplicado:
   revisa el sidecar y vuelve a seleccionar/pegar y guardar el archivo. Después valida
   el tráfico desde un cliente; **Configuración cargada** no prueba conectividad VPN.

El archivo contiene claves privadas: introdúcelo solo en el panel de confianza, no en
el chat, Git ni incidencias. **Borrar** elimina la configuración y, con el driver virtual
seleccionado, solicita detener el túnel. Con UniFi, Omada, OPNsense o MikroTik, configura
la VPN en el router; esta pestaña no carga archivos en esos equipos.

### Configurar el dispositivo

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
- [Uso del panel, destinos, listas y fuentes](docs/USER_GUIDE.md)
- [Referencia de todas las opciones](docs/CONFIGURATION.md) y [API](docs/API.md)
- [Operación, diagnóstico, copias y actualización](docs/OPERATIONS.md)
- [Desarrollo y comprobaciones](CONTRIBUTING.md)
- [Índice técnico e histórico](docs/README.md)

## Licencia y créditos

AGPL-3.0-or-later. Gracias a [jcastro/stopliga](https://github.com/jcastro/stopliga)
y a la comunidad [hayahora.futbol](https://hayahora.futbol).
[Dependencias y atribuciones](THIRD_PARTY_NOTICES.md).
