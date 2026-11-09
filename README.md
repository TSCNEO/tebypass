# TeByPass

**Tu internet normal, salvo lo que te bloquean.**

TeByPass manda por una VPN **solo** el tráfico que tu operador bloquea (fútbol, LaLiga, dominios concretos). Todo lo demás sigue saliendo por tu fibra, a velocidad completa y sin pasar por ningún túnel.

---

## El problema

Tu operador bloquea ciertas webs y servicios: las páginas de fútbol, algunos dominios de LaLiga, ciertos CDNs. Desde tu casa aparecen caídas o inaccesibles, y no hay nada que puedas tocar en el móvil o en la tele para arreglarlo.

La solución habitual es meter **toda la casa** por una VPN. Y eso trae factura: pierdes velocidad, sube la latencia, y se rompen cosas que funcionaban perfectamente (la banca, Netflix, la TV de la operadora) y aparecen comprobaciones que antes no saltaban.

---

## La solución: un bypass quirúrgico

TeByPass no desvía tu casa entera. Desvía **solo las direcciones IP que están bloqueadas**. El resto del tráfico ni se entera de que existe.

Piensa en un desvío en la carretera: cuando cortan un tramo, no te obligan a dar la vuelta a la provincia entera. Te desvían por la variante, pasas ese tramo, y vuelves a tu ruta. El resto del viaje es exactamente igual de rápido que siempre.

Eso es todo. Unas pocas IPs van por el túnel. Tus descargas, tu Netflix, tu trabajo y tus videollamadas van por donde iban.

---

## Ventajas

- **Velocidad intacta** para todo lo que no está bloqueado. La VPN solo transporta lo imprescindible: unos pocos rangos de IP, no tu tráfico doméstico.
- **Cero apps y cero certificados** en la tele, el móvil o el PC. Se resuelve en el router o en un pequeño gateway de la red: tus dispositivos no configuran nada.
- **Funciona con lo que ya tienes**: router avanzado (UniFi, MikroTik) o router de operadora usando el modo gateway virtual.
- **Se adapta solo**: cuando un CDN cambia sus IPs, el sistema vuelve a resolver y actualiza la lista. La ventana deslizante evita que una IP desaparezca en mitad de un partido.
- **Lista comunitaria de LaLiga** (feed de HayAHora) más tus propias listas de dominios. Puedes mezclarlas o usar solo las tuyas.
- **Todo se configura desde la web**: router, credenciales, intervalos y WireGuard. Sin editar ficheros ni reiniciar contenedores.
- **Versión dinámica visible** en la interfaz: siempre sabes qué build estás corriendo y con qué commit.
- **Control si el túnel cae**: eliges entre cortar el tráfico afectado (fail-closed) o dejarlo salir por la conexión normal.

---

## Cómo funciona por dentro

Para quien tenga curiosidad, el ciclo es corto y repetitivo:

1. **Resuelve los dominios con tu propio DNS.** Usa el resolver del sistema, así obtiene exactamente las mismas IPs que ve tu red (y respeta tu Pi-hole, AdGuard o el DNS de tu operador).
2. **Guarda esas IPs en una ventana deslizante.** Cada IP resuelta tiene una caducidad que se va desplazando mientras el destino siga activo.
3. **Calcula la diferencia** entre lo que debería haber en el router y lo que hay ahora mismo.
4. **Aplica solo los cambios.** Si no hay diferencia, no toca nada.

El proceso es **idempotente**: ejecutarlo diez veces seguidas produce el mismo resultado que ejecutarlo una. Y nunca borra una IP demasiado pronto: prefiere mantener cobertura de más antes que cortarte el stream.

---

## Arranque rápido con Docker

```bash
git clone https://github.com/TSCNEO/tebypass.git
cd tebypass
docker compose up -d
```

Abre **http://localhost:9384** y configura todo desde la interfaz: driver de router, credenciales, la lista comunitaria, tus dominios y el túnel.

No hace falta editar `.env` ni `docker-compose.yml`. La configuración vive en una base de datos SQLite dentro del volumen de datos (`/data`), así que sobrevive a reinicios y actualizaciones de la imagen. Solo hay dos variables de entorno opcionales, y únicamente si quieres cambiar dónde escucha o dónde guarda:

```bash
TEBYPASS_PORT=9384
TEBYPASS_DATABASE_URL=sqlite+aiosqlite:////data/tebypass.db
```

Si no las defines, se usan esos mismos valores por defecto.

---

## Los dos modos

| | **Modo Router** (recomendado) | **Modo Gateway virtual** |
| :--- | :--- | :--- |
| **Para qué** | Ya tienes UniFi o MikroTik | Router de operadora, sin opciones avanzadas |
| **Cómo funciona** | TeByPass crea y mantiene una lista de IPs en el propio router (firewall group en UniFi, address-list en MikroTik) | Un contenedor Alpine con WireGuard y nftables hace de puerta de enlace para los dispositivos que quieras |
| **Dónde va el túnel** | En el router | En el contenedor `tebypass-gateway` |
| **Qué toca tu red** | Nada más que la lista de IPs | Los dispositivos apuntan su puerta de enlace al gateway |
| **Ventaja** | Sin saltos extra, cero latencia añadida | No necesitas cambiar de router |

En el modo gateway, el sidecar se levanta con el perfil `gateway`:

```bash
docker compose --profile gateway up -d
```

---

## La interfaz, en cuatro ideas

- **Activar o desactivar la lista comunitaria** de LaLiga con un clic. Si el feed dice que no hay bloqueo activo, no enruta nada: no gasta VPN sin motivo.
- **Crear listas propias** (por temas: streaming, trabajo, lo que sea) y añadir dominios o rangos a mano, uno a uno o en bloque.
- **Ver las IPs resueltas** de cada destino y forzar una resolución cuando quieras comprobar algo.
- **Subir tu `wg0.conf`** desde el navegador: se valida, se guarda con permisos restringidos y la vista previa oculta las claves privadas.

---

## Preguntas frecuentes

**¿Necesito saber de redes?**
No. Si sabes abrir el panel de tu router y copiar una URL, sabes usar TeByPass. Los conceptos raros (CIDR, ventana deslizante, reconciliación) los gestiona el programa.

**¿Va más lento mi internet?**
Solo lo que pasa por la VPN, que es una minoría del tráfico. Una descarga, una videollamada o un partido por la tele de la operadora no tocan el túnel.

**¿Se rompe Netflix, la banca o la TV de la operadora?**
No. No pasan por el túnel, así que te ven con tu IP de siempre. Ese es justamente el motivo de existir de este proyecto.

**¿Y si el operador o un CDN cambia las IPs?**
Se actualiza solo en el siguiente ciclo: vuelve a resolver los dominios, compara y aplica la diferencia. No tienes que hacer nada.

**¿Dónde se guardan mis credenciales?**
En tu SQLite local, dentro de tu volumen de datos. No salen de tu máquina, y la API las devuelve enmascaradas cuando las consultas.

**¿Funciona en cualquier red?**
Sí. Es agnóstico: no hay IPs, DNS ni routers hardcodeados. Todo se configura en la interfaz, y los ejemplos de la documentación usan dominios y rangos reservados por la IETF (RFC 2606 y RFC 5737).

---

## Arquitectura para desarrolladores

El proyecto sigue la regla de **0 monolitos**: cada capa tiene una responsabilidad y un contrato claro.

| Ruta | Qué hay dentro |
| :--- | :--- |
| `app/core` | Motor de reconciliación, resolver DNS nativo, caché de ventana deslizante, ingesta de feeds y configuración |
| `app/drivers` | Implementaciones de `BaseRouterDriver`: `mock`, `virtual_gateway`, `unifi`, `mikrotik` |
| `app/db` | SQLAlchemy 2.0 + `aiosqlite` sobre SQLite en modo WAL |
| `app/api` | Endpoints REST: estado, targets, feeds, carpetas, ajustes y WireGuard |
| `app/templates` | Panel web en Jinja2 + Alpine.js, sin Node.js en runtime |
| `gateway/` | Sidecar Alpine con nftables y WireGuard (`wg0`), independiente del runtime de Python |

Documentación de detalle:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — componentes, contratos y modelo de datos.
- [docs/DECISIONS.md](docs/DECISIONS.md) — decisiones de arquitectura y por qué se tomaron.
- [docs/ROADMAP.md](docs/ROADMAP.md) — fases y estado del proyecto.
- [AGENTS.md](AGENTS.md) — normas de trabajo, calidad y privacidad del repositorio.

Desarrollo local:

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check . && mypy app tests
```

---

## Créditos

Este proyecto no sería posible sin el trabajo de otras personas, hecho a menudo gratis y en su tiempo libre:

- **jcastro / stopliga** — por la catalogación, la documentación y la transparencia frente a los bloqueos arbitrarios. Buena parte de lo que sabemos de cómo se comportan estos bloqueos viene de ahí.
- **Comunidad de hayahora.futbol** — por mantener vivas y abiertas las listas que alimentan la detección de bloqueos de LaLiga.

Gracias de verdad. Si TeByPass te resulta útil, el mérito de los datos es suyo.

---

## Aviso legal

TeByPass es una **herramienta de enrutamiento**: decide por dónde sale cada paquete. No incluye contenido, no descifra nada y no salta ninguna suscripción.

Úsala solo para acceder a contenido para el que tengas derecho de acceso. No está pensada para evadir suscripciones, paywalls ni medidas de protección de contenido. Lo que hagas con ella es responsabilidad tuya y debe cumplir la ley de tu país y las condiciones de los servicios que uses.

---

## Licencia

MIT. Consulta [LICENSE](LICENSE).
