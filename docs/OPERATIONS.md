# Operación del MVP

## Acceso y secretos

Primera instalación: abre el panel e introduce la clave que genera el servicio:

```sh
docker compose exec tebypass cat /data/admin_token
```

No se muestra en logs ni endpoints. Para revocar todas las sesiones, detén la aplicación,
retira ese fichero dentro del volumen y arranca de nuevo: genera otra clave. Conserva
`/data/.secret_key` para descifrar credenciales; no la regeneres para rotar sesiones.
El API acepta Bearer con la misma clave. El navegador usa cookie firmada de 12 horas.

Guarda copia consistente de SQLite (API backup de sqlite3) junto con `gateway_targets.json`, `.secret_key`,
`admin_token` y `wireguard/wg0.conf`, con acceso restringido. No copies solo el `.db`
mientras hay escritores sin usar backup o detener el servicio: puede haber datos en WAL.

## Preparación por router

- **UniFi:** API key local, cliente VPN creado; selecciona VPN en Ajustes. Soporte V2.
  La política usa todos los clientes que el router alcance; no configura cada dispositivo.
- **Omada:** activa Open API, registra Client ID/Secret y Controller/Omada ID. Introduce
  sitio y tipo/nombre o ID de WAN/VPN. El backend gestiona grupos propios y política.
- **OPNsense:** prepara alias destino con el nombre configurado y una regla en
  Firewall → Rules [new] / Automation → Filter que lo use y seleccione tu gateway VPN.
  La descripción debe coincidir exactamente. Reglas legacy pueden no ser visibles por API.
  TeByPass activa/desactiva esa regla; no inventa su topología ni su gateway.
- **MikroTik:** configura REST HTTPS, una tabla FIB y ruta por defecto hacia la VPN.
  Introduce el nombre de tabla. Las reglas gestionadas se identifican con comentario propio
  y usan lookup-only-in-table. FastTrack, mangle y prioridad de reglas existentes deben
  permitir ese recorrido. No se alteran reglas ajenas ni se crea el túnel.

Al cambiar identidad de router/política se retira primero lo anterior. Si el dispositivo
anterior no responde, se rechaza el cambio: restablece conectividad y vuelve a intentarlo.
Si se produjo un fallo después de retirar reglas, vuelve a sincronizar para restaurarlas.
No hay atomicidad distribuida entre dispositivos y base de datos.

## Gateway virtual: despliegue en la LAN

1. Descubre en el host Linux la interfaz física/bridge, subred y router (`ip -br addr`,
   `ip route`). Elige con el administrador una dirección libre fuera del pool DHCP;
   confirma disponibilidad mediante ARP y la tabla DHCP, no solo ausencia de ping.
2. Crea macvlan con los parámetros elegidos. Ejemplo **solo de documentación**:

```sh
docker network create -d macvlan --subnet=192.0.2.0/24 --gateway=192.0.2.1 -o parent=eth0 tebypass-lan
```

3. Configura `.env` local, con valores reales de tu entorno en lugar de los reservados:

```dotenv
TEBYPASS_LAN_NETWORK=tebypass-lan
TEBYPASS_GATEWAY_IP=192.0.2.26
TEBYPASS_GATEWAY_MAC=02:42:c0:00:02:1a
```

**Reserva la IP del Gateway Virtual en el DHCP de tu router, asociada a la MAC configurada,
y ponle una nota “TeByPass-Gateway”.** La MAC es determinista para mantener esa reserva.

4. Arranca el perfil y selecciona Gateway virtual en Ajustes:

```sh
docker compose --profile gateway up -d --wait
```

5. Sube `wg0.conf` desde la web. El controlador valida, configura `Table=off`, elimina
   DNS del archivo aplicado y rechaza hooks. Guardar/aplicar y borrar/detener son operaciones
   distintas del mero estado “fichero presente”. Un 502 exige revisar y volver a aplicar.
6. Prueba desde **otro equipo LAN**: ping a la IP elegida y Probar conexión. En macvlan el
   host normalmente no puede alcanzar directamente al contenedor. Revisa estado del socket,
   WireGuard, handshake y eventos desde la API autenticada.
7. Para una TV/deco, usa la IP del gateway como puerta de enlace manual y el DNS habitual.
   En macOS puedes probar un solo prefijo temporal antes de cambiar más clientes:

```sh
sudo route add -net 198.51.100.0/24 192.0.2.26
sudo route delete -net 198.51.100.0/24 192.0.2.26
```

La aplicación y sidecar deben compartir volumen en el mismo host. Solo IPv4. El ajuste
fail-closed se aplica al gateway: no modifica por sí mismo kill-switches de otros routers.
El túnel necesita AllowedIPs compatibles con los destinos seleccionados.

## Estado y fallos

- `/healthz`: proceso HTTP disponible; deliberadamente no certifica router ni VPN.
- `/api/v1/status`: salud detallada, último intento/error y contadores. Requiere login.
- Fuente en error: se conserva snapshot anterior y aparece degradación. Revisa URL,
  timestamp de origen y límites. HTTP 304 no reinicia la antigüedad de `lastUpdate`.
- Pausa de feed: retira su contribución, sin tocar manuales. Vaciar un feed también lo
  pausa para impedir que se rellene inmediatamente. Pausa master retira todo.
- Sincronización fallida con total -1: resultado remoto desconocido, no “cero rutas”.
- Intervalo 0: modo manual. Los cambios web siguen solicitando reconciliación.
- Horarios limitan contribuciones de feeds; no prueban que haya bloqueo. El webhook HTTPS
  notifica transiciones sin destinos ni credenciales; reintenta por ciclo si no confirma
  entrega. Un timeout puede duplicar un aviso (entrega al menos una vez).

## Actualizar y revertir

Las instalaciones de release fijan una versión de imagen en su Compose. Guarda backup
consistente de BD/secretos y conserva la versión anterior antes de actualizar. Descarga
el Compose de la nueva release, revisa cambios y después:

```sh
docker compose pull
docker compose up -d --wait
```

Si utilizas el sidecar, añade `--profile gateway` a ambos comandos. También puedes
seleccionar una versión de ambas imágenes en `.env`, por ejemplo `TEBYPASS_IMAGE_TAG=0.2.0`.
`latest` solo se promueve con tags estables; `edge` sigue main y no es un canal estable.
Los tags `sha-<12 caracteres>` identifican el código de main; `build-<run>-<attempt>`
identifica una ejecución concreta, diferenciando builds de main y de release. Las imágenes
son Linux amd64/arm64. El gateway requiere kernel Linux con nftables y WireGuard.

Para rollback recupera el Compose/valor de versión anterior y repite pull/up. No elimines
los volúmenes. Las migraciones del MVP son aditivas; restaurar un backup de BD requiere
detener ambos servicios y mantener su clave correspondiente. No resetees reglas ajenas.

En despliegues que compilan el código, usa siempre ambos archivos:

```sh
git pull --ff-only
TEBYPASS_GIT_COMMIT=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

El override genera `tebypass:local` y `tebypass-gateway:local`, sin publicar ni descargar
imágenes de release. Comprueba versión del build, login, guardado, salud del driver y
sincronización después de cada actualización. El puerto interno permanece en 9384.

## Verificación Linux aislada

```sh
docker build -f gateway/Dockerfile -t tebypass-gateway:verify .
docker run --rm --cap-add NET_ADMIN -e PYTHONPATH=/ --entrypoint python3 \
  -v "$PWD/scripts/verify_gateway_network.py:/verify.py:ro" tebypass-gateway:verify /verify.py
```

Ese contenedor no comparte el namespace de red del host ni volúmenes de producción.
Comprueba reglas nft reales, descarte de tráfico seleccionado sin túnel, recorrido normal
para no seleccionado, fail-open explícito y vaciado del set. No sustituye una prueba con
clientes LAN y un proveedor WireGuard real. También crea y retira una interfaz WireGuard
con claves efímeras y parámetros de documentación para comprobar su ciclo de vida.

Base del sidecar: Alpine 3.24, rama con soporte según [el calendario oficial](https://alpinelinux.org/releases/).

## Un cliente usa el gateway pero no navega

Configura como puerta de enlace la **IP LAN propia del contenedor**. Puedes consultarla
sin confundirla con la dirección del servidor del panel:

```sh
docker inspect tebypass-gateway --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

El cliente necesita además IP libre y máscara correctas, y un DNS accesible (por ejemplo,
el DNS habitual del router). El gateway no ejecuta un servidor DNS: usar su IP como DNS
no funciona salvo que se añada un servicio DNS externo expresamente. Desactiva datos
móviles durante la prueba y verifica que el Wi-Fi permita alcanzar otros equipos LAN
(red de invitados/aislamiento de clientes puede impedirlo).

Seleccionar `virtual_gateway` en Ajustes dirige los destinos administrados al sidecar.
Usar su IP como gateway del cliente solo cambia el recorrido del cliente: no cambia el
driver del motor. Con el driver UniFi seleccionado el sidecar no recibe sus destinos.
Sin destinos seleccionados, el gateway debe seguir reenviando tráfico por la salida normal.

Una conexión HTTP desde el contenedor no acredita el reenvío de clientes. La prueba
adicional de CI crea una LAN Docker aislada, un cliente y un endpoint DNS/HTTP de laboratorio:

```sh
python scripts/verify_gateway_forwarding.py --image tebypass-gateway:verify
```

Comprueba tráfico que entra y sale por la misma interfaz, vuelta por NAT, DNS y HTTP
directos y a través de un peer WireGuard con claves efímeras. Verifica transferencias de
256 KiB en ambos sentidos, caída del túnel, fail-closed y recuperación al retirar el destino. Se eliminan los
contenedores y la red de prueba; no modifica la LAN ni usa volúmenes del despliegue.

## Traefik y otros proxies HTTPS

El acceso web **no necesita configurar la IP del proxy ni ejecutar un asistente**.
Con el proxy apuntando al puerto interno 9384 y conservando `Host`, abre el panel e
inicia sesión. El formulario envía automáticamente `location.origin`; el servidor
valida la clave administrativa, la coincidencia con la cabecera Origin y el Host
conservado por el proxy. La URL queda firmada dentro de esa sesión, no como un valor
global que pueda fijar el primer visitante. HTTPS recibe cookie Secure aunque el salto
interno sea HTTP. Cada guardado exige el origen firmado completo (esquema, host y puerto).

Funciona también con acceso HTTP directo y puertos personalizados. El mismo servicio
puede usarse desde distintas URLs con sesiones independientes. Una sesión anterior a
esta versión conserva el control anterior; vuelve a iniciar sesión para adoptar el
reconocimiento automático. No hay cambios de `.env`, reinicios ni acceso al socket
Docker desde la aplicación. La API Bearer mantiene su contrato.

El proxy debe estar publicado con certificado válido y conservar Host; el reconocimiento
no crea certificados ni configura la ruta inicial del proxy. Si el login devuelve un
error de origen, comprueba que el proxy conserve Host. No se acepta X-Forwarded-Host
arbitrario como sustituto.

### Opcional: metadatos de conexión del proxy

La confianza explícita sigue disponible si necesitas que el esquema efectivo de Uvicorn
(`/healthz.scheme`) y los datos reenviados de conexión reflejen el proxy. **No es un paso
necesario para usar el panel.** Puedes ejecutar desde el checkout Compose del servidor:

```sh
python3 scripts/setup_proxy.py 'https://tebypass.example.org'
python3 scripts/setup_proxy.py 'https://tebypass.example.org' --apply
```

Requiere Python >= 3.10 y acceso Docker. El primer comando diagnostica sin modificar;
`--apply` identifica la conexión mediante una petición marcada en logs, guarda copia
privada `.env.before-proxy-setup`, recrea solo la aplicación y verifica. Restaura si
falla; informa si tampoco puede recrear la configuración anterior. No modifica gateway
ni política. La salida JSON usa `ready`, `needs_configuration`, `applied` o `error` (salida 1).
No lee credenciales. `--help` describe nombres de servicio/contenedor personalizados.

Configuración avanzada manual: en el `.env` del host indica las direcciones de
**conexión del proxy a la aplicación**:

```dotenv
# Dirección de documentación; sustituye por la del proxy de tu despliegue.
TEBYPASS_TRUSTED_PROXIES=192.0.2.10
```

Compose la pasa a Uvicorn como `FORWARDED_ALLOW_IPS`. Admite una lista separada por comas
con IPs o CIDRs. Reinicia/recrea la aplicación después de cambiarla. Con Docker y NAT,
la conexión puede llegar desde el gateway del bridge del servicio en lugar de la IP del
contenedor Traefik; verifica la dirección realmente observada en el backend.

Traefik debe conservar `Host` y establecer `X-Forwarded-Proto: https`. Solo los proxies
incluidos en esa lista pueden determinar el esquema externo. Esto permite reflejar el HTTPS externo
en los metadatos de la aplicación; las sesiones del panel ya lo reconocen por separado.
No configures confianza global `*` con el puerto interno accesible a otros clientes.

La comprobación CSRF compara esquema, host y puerto con el origen firmado de la sesión;
normaliza puertos predeterminados y rechaza orígenes distintos o ausentes en sesiones nuevas. No interpreta `X-Forwarded-Host`
arbitrario como una autorización. Véase [FastAPI detrás de un proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/).
