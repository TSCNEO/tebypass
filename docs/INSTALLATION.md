# Instalación

## 1. Elegir el recorrido del tráfico

TeByPass tiene un plano de control (el panel y el motor) y una salida de tráfico.
El servidor del panel no necesita ser la puerta de enlace de los clientes cuando
el router compatible aplica las políticas.

- **Router compatible con VPN/salida disponible:** elegir su driver y ejecutar solo
  `tebypass`. No crear macvlan, subir WireGuard al panel ni cambiar gateways de clientes.
- **Router sin integración adecuada:** valorar `virtual_gateway`. Requiere Linux,
  una dirección propia en la LAN y clientes que puedan usarlo como puerta de enlace.
- **Exploración:** elegir Mock. Simula políticas; no desvía paquetes.

Tener un controlador de una marca no basta: el gateway y su API deben ofrecer las
funciones requeridas. Si faltan, revisar las alternativas antes de crear servicios.

## Requisitos por driver

| Driver | Preparar antes de activar | Gateway Docker |
|---|---|---|
| UniFi | API key local, API Traffic Routes V2 y cliente VPN existente | No |
| Omada | Open API, Client ID/Secret, Controller ID, sitio y WAN/VPN existente | No |
| OPNsense | Credenciales API, alias destino y regla en Rules [new]/Automation Filter con descripción exacta y gateway VPN | No |
| MikroTik | REST RouterOS v7, tabla FIB y ruta por defecto a la VPN; revisar FastTrack/mangle | No |
| Virtual gateway | Linux con nftables/WireGuard y NET_ADMIN, macvlan, IP libre, MAC fija y configuración WireGuard | Sí |
| Mock | Ninguno; solo simulación | No |

La preparación específica se detalla en [Operación](OPERATIONS.md#preparación-por-router).
No se crean VPNs de router ni se compran credenciales desde TeByPass. Una sola salida
por instancia. Gateway, MikroTik y Omada solo aceptan IPv4. El gateway no funciona directamente
sobre el kernel de macOS/Windows; requiere un host Linux con acceso adecuado a la LAN.

## 2. Obtener la aplicación

### Release pública

Consulta [Releases](https://github.com/TSCNEO/tebypass/releases). Si hay una entrega
publicada, descarga su `docker-compose.yml` en un directorio persistente y comprueba
sus checksums con `SHA256SUMS`. El Compose adjunto fija las imágenes de esa versión.
No es necesario clonar, instalar Python ni compilar:

```sh
docker compose pull tebypass
docker compose up -d --wait tebypass
```

Si no hay entrega publicada, esta vía todavía no está disponible. Un borrador no es
accesible para visitantes. La visibilidad pública del repositorio tampoco garantiza
que sus paquetes GHCR sean públicos. Ante un error de descarga, comprueba versión y
visibilidad antes de solicitar credenciales de registro.

### Compilar el código

Requiere Git y Docker con capacidad de build. Desde un checkout del repositorio:

```sh
git clone https://github.com/TSCNEO/tebypass.git
cd tebypass
TEBYPASS_GIT_COMMIT=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait tebypass
```

Conserva ambos `-f` en futuras operaciones de despliegue: usar solo el Compose base
volvería a seleccionar imágenes publicadas. El build no necesita Node en runtime.
No utilices `edge` como si fuera una versión estable; sigue los cambios de main.

## 3. Acceder al panel

Abre `http://<servidor>:9384`. La clave se genera en el volumen persistente:

```sh
docker compose exec tebypass cat /data/admin_token
```

Introduce la clave en el formulario. No la publiques ni la pegues en incidencias.
Para cambiar el puerto publicado, crea `.env` local con `TEBYPASS_PORT` antes de arrancar;
el puerto interno continúa siendo 9384. La configuración operativa se guarda desde la
web, no en `.env`.

Para acceso por proxy HTTPS, publica una ruta con certificado y destino al puerto 9384,
conservando `Host`. Inicia sesión desde esa URL. Consulta el
[procedimiento de proxy](OPERATIONS.md#traefik-y-otros-proxies-https) si hay errores.
No expongas el panel por HTTP en redes no confiables.

## 4. Configurar la salida

1. En Ajustes, seleccionar el driver elegido y completar sus campos.
2. Probar conexión y revisar el estado detallado, no solo el healthcheck HTTP.
3. Añadir un destino controlado para una prueba y sincronizar.
4. Comprobar que la política aparece en el router y que un cliente usa la salida esperada.
5. Comprobar que tráfico no seleccionado conserva su salida y que pausar retira la política.
6. Activar las fuentes deseadas, revisar sus destinos y ajustar el intervalo.

El feed inicial está pausado. Los destinos manuales habilitados siguen contribuyendo
cuando la señal comunitaria no está activa. Un error de fuente conserva el último
snapshot válido y aparece como degradación; no significa que se hayan retirado rutas.
Los FQDN conservan direcciones durante su ventana de retención aunque cambie DNS.

## 5. Solo si se ha elegido gateway virtual

Completar el [procedimiento LAN](OPERATIONS.md#gateway-virtual-despliegue-en-la-lan)
antes de arrancar el perfil: interfaz padre, subred, router, pool DHCP, dirección libre,
MAC determinista y reserva DHCP. Los rangos de los ejemplos son de documentación;
no sirven como configuración del entorno.

Para imágenes de release:

```sh
docker compose --profile gateway up -d --wait
```

Para un checkout compilado:

```sh
TEBYPASS_GIT_COMMIT=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.build.yml --profile gateway up -d --build --wait
```

Seleccionar `virtual_gateway` en Ajustes y pulsar **Guardar ajustes**. Después abrir
**Ajustes → WireGuard**, seleccionar el archivo de cliente `.conf` del proveedor o pegar
su contenido y pulsar **Guardar configuración**. Elegir un archivo no lo sube hasta
pulsar ese botón. Comprobar **Túnel aplicado**; si devuelve 502, revisar el sidecar y
volver a cargar/guardar para aplicar. El indicador **Configuración cargada** solo acredita
que existe un archivo, no conectividad. No compartir sus claves en chat ni Git.

Aplicación y sidecar
deben compartir el volumen en el mismo host. Usar la IP LAN del sidecar como gateway
del cliente de prueba y mantener un DNS accesible: el sidecar no es servidor DNS.
Verificar tráfico seleccionado/no seleccionado y caída del túnel desde otro equipo LAN.
Un ping desde el host puede fallar por el aislamiento propio de macvlan.

## 6. Conservar y mantener

Guardar copia consistente del volumen, incluida la clave de cifrado y WireGuard.
Seguir [actualización y reversión](OPERATIONS.md#actualizar-y-revertir). No ejecutar
`docker compose down -v` para actualizar: elimina los datos persistentes.
