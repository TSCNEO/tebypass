# Alcance y limitaciones

TeByPass administra destinos L3 mediante un router compatible o un gateway virtual.
Se configura una política de salida, un driver y un worker por instalación.

## Comportamiento disponible

- Destinos manuales FQDN/CIDR, importación, listas organizativas y fuentes de destinos.
- UniFi Traffic Routes V2, Omada/OPNsense mediante adaptador, MikroTik REST y gateway
  virtual nftables/WireGuard; cada uno requiere la [preparación correspondiente](INSTALLATION.md#requisitos-por-driver).
- Pausa global y de fuentes, sincronización manual/periódica, retención DNS persistente,
  horarios comunitarios y webhook opcionales.
- Los destinos manuales habilitados contribuyen independientemente de la señal comunitaria.
  Un error de fuente conserva el último snapshot válido y aparece como degradación.
- El gateway comparte host y volumen con la aplicación; los clientes que lo usen deben
  tenerlo como puerta de enlace. Con driver de router compatible conservan su gateway.

## Restricciones

- Gateway, MikroTik y Omada solo admiten IPv4. Los ajustes y destinos incompatibles se rechazan.
- Las carpetas no eligen salidas diferentes. No hay pools VPN, failover entre varias VPN
  ni balanceo implementados. TeByPass no crea la VPN del router ni suministra credenciales.
- Una IP compartida por distintos dominios se enruta como una sola IP. No hay selección
  por URL ni inspección de conexiones de aplicaciones.
- DNS del servidor y del cliente pueden devolver IPs distintas. Las IPs antiguas se
  conservan durante su retención; la aplicación no observa cuánto duran las conexiones.
- Fail-closed del gateway bloquea destinos seleccionados sin ruta por el túnel. No
  configura automáticamente esa protección en otros routers. Ver [Configuración](CONFIGURATION.md).
- Cambiar de router/política necesita retirar lo anterior; si falla la retirada, se rechaza
  el cambio. No existe una transacción distribuida entre router y SQLite.

## Cómo verificar una instalación

La salud HTTP/Docker acredita el proceso, no el acceso de clientes por VPN. Una interfaz
WireGuard presente o un handshake tampoco acredita todos los destinos.

La distribución incluye pruebas de contrato y laboratorio Linux. No equivalen a certificar
cada modelo o firmware. Verificar en el entorno elegido: política efectiva, tráfico
seleccionado/no seleccionado, pausa, reinicio y recuperación. Seguir [Instalación](INSTALLATION.md)
y [Operación](OPERATIONS.md) para el recorrido aplicable.
