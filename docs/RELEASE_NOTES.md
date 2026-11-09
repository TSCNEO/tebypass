Primera entrega MVP de TeByPass: routing selectivo por destino con panel web en español,
API autenticada y configuración persistente.

- Instalación con Docker Compose e imágenes GHCR, sin compilar en el servidor.
- Señal comunitaria separada de destinos: sin bloqueo global se retira su contribución.
- Drivers UniFi V2, Omada, OPNsense, MikroTik y gateway virtual nftables/WireGuard.
- Login que reconoce automáticamente la URL externa, incluido HTTPS mediante Traefik.
- Gateway con socket local, reenvío/NAT y protección fail-closed de destinos seleccionados.

Una política de salida por instalación. Gateway/MikroTik IPv4. El gateway requiere Linux,
macvlan e IP propia; la VPN y la preparación del router corresponden al administrador.
UniFi cuenta con evidencia en equipo real; otros routers se verifican mediante contratos,
mocks y lógica upstream. No se afirma certificación de todos los modelos/firmwares.

Configura la salida y prueba conexión antes de activar el feed, inicialmente pausado.
Guarda BD y claves antes de actualizar; no elimines el volumen para actualizar o revertir.

Gracias a [jcastro/stopliga](https://github.com/jcastro/stopliga) por la idea y a la comunidad
[hayahora.futbol](https://hayahora.futbol) por mantener las listas abiertas.
