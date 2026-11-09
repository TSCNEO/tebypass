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
docker compose --profile gateway up -d --build
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

Guarda backup consistente y etiqueta la imagen anterior antes de actualizar. Comprueba
que el checkout no tenga modificaciones locales, luego:

```sh
git pull --ff-only
TEBYPASS_GIT_COMMIT=$(git rev-parse HEAD) docker compose up -d --build
```

Si utilizas el sidecar, añade `--profile gateway`. Verifica liveness, acceso, versión del
build, estado detallado y sincronización; un `git pull` sin build ya no cambia la versión
anunciada. El puerto interno es fijo 9384; `.env` cambia el puerto publicado.

Para rollback usa la imagen guardada con un override Compose de `image` y evita construir
encima de ella. Las migraciones del MVP son aditivas y compatibles con la entrega previa;
restaurar un backup de BD debe hacerse con ambos servicios detenidos. Mantén siempre su
clave correspondiente. No hagas reset de reglas ajenas como parte de una reversión.

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
