# TeByPass

MVP de enrutamiento selectivo: desvía destinos concretos por una salida VPN y conserva
el resto del tráfico en su recorrido habitual. Panel en español, API FastAPI y SQLite.

## Comportamiento actual

- La lista comunitaria usa el JSON canónico de HayAHora y la interpretación de señal global
  de una revisión fija de [stopliga](https://github.com/jcastro/stopliga).
- **Sin bloqueo global, su contribución queda desactivada**, aunque haya entradas residuales.
  Los destinos manuales habilitados siguen activos. Una misma IP compartida por ambas fuentes
  se conserva mientras la necesite una contribución habilitada.
- Un error, documento obsoleto o respuesta inválida conserva el último snapshot válido y
  aparece como degradación; nunca se interpreta silenciosamente como “sin bloqueos”.
- Los FQDN manuales conservan cada IP hasta vencer su ventana de retención, incluso si
  cambia DNS, falla temporalmente o reinicia la aplicación. El TTL usado es sintético.
- Un solo driver y una sola política de salida por instalación; no hay múltiples VPN
  simultáneas por carpeta. Las carpetas organizan destinos, no definen salidas.
- Pausa global, apagado verificado, sincronización manual, intervalo reactivo (0 = manual),
  horarios comunitarios opcionales y webhook de transiciones.

## Routers y requisitos

| Driver | Implementación | Preparación necesaria |
|---|---|---|
| UniFi | API V2 Traffic Routes, VPN seleccionable, comprobación posterior | API key local y cliente VPN ya creado |
| Omada | Adaptador de stopliga: Open API, grupos y política, verificación/rollback | Client ID/Secret, Controller ID, sitio y WAN/VPN existente |
| OPNsense | Adaptador de stopliga: alias y activación de regla, verificación/rollback | Regla en Rules [new]/Automation Filter; descripción exacta, alias destino y gateway VPN |
| MikroTik | REST RouterOS v7, reglas propias lookup-only-in-table | Tabla FIB y ruta por defecto a la VPN; FastTrack/mangle compatibles |
| Gateway virtual | Sidecar IPv4 con nftables + WireGuard y socket Unix | Linux con NET_ADMIN, macvlan y dirección propia en la LAN |
| Mock | Simulación explícita | Ninguno |

UniFi se valida también contra el router del entorno autorizado. Omada, OPNsense y MikroTik
se verifican con pruebas de contrato/mocks y lógica upstream; no se afirma certificación en
hardware de esos fabricantes. El soporte UniFi del MVP se limita a V2 Traffic Routes; no
incluye todos los formatos legacy de stopliga. El gateway y MikroTik del MVP son IPv4.

## Arranque

```sh
git clone https://github.com/TSCNEO/tebypass
cd tebypass
docker compose up -d --build
```

Abre el puerto 9384 del host. La aplicación genera una clave de administración local:

```sh
docker compose exec tebypass cat /data/admin_token
```

Introdúcela en la pantalla de acceso. No la guardes en Git. API: cabecera
`Authorization: Bearer <CLAVE_ADMIN>`. Sesiones de navegador de 12 horas con cookie
HttpOnly/SameSite; autenticación exigida también para leer ajustes y destinos. Solo
`/healthz`, el formulario de acceso y assets son públicos. Para acceso a través de redes
no confiables, publica el servicio mediante HTTPS en tu proxy.

Configura el driver desde Ajustes y pulsa Probar conexión. El feed inicial está **pausado**:
actívalo cuando hayas elegido la salida. Crear una VPN, obtener credenciales del proveedor
VPN y preparar las políticas base del router corresponde al administrador.

**Traefik y otros proxies HTTPS:** abre el panel e inicia sesión. El navegador comunica
su URL y la sesión la reconoce automáticamente; no necesitas indicar el servidor,
configurar IPs de proxies ni ejecutar comandos adicionales. El proxy debe conservar
`Host` y dirigir el tráfico al puerto interno 9384. Certificado y ruta del proxy se
preparan en el propio proxy. Si tenías una sesión antigua, vuelve a entrar una vez.
Consulta la [guía operativa](docs/OPERATIONS.md#traefik-y-otros-proxies-https) o la
[guía para LLM](docs/LLM_DEPLOYMENT.md).

Puerto y ubicación de BD son bootstrap. Credenciales, fuentes, intervalos, horarios y
comportamiento se guardan desde la web. Las opciones de macvlan son parámetros Docker.
La dirección de escucha interna del contenedor permanece en 9384 aunque personalices el
puerto publicado con `TEBYPASS_PORT` en `.env`.

## Gateway virtual

Consulta [la guía operativa](docs/OPERATIONS.md). Requiere seleccionar la interfaz física,
la subred, el router y una dirección libre del entorno; no uses los valores de documentación
como configuración real. Mantén una MAC determinista y reserva la dirección en DHCP.
La aplicación y el sidecar comparten un volumen y socket Unix; **no se admite el sidecar
en otro host** en este MVP.

Subir WireGuard lo valida y, si el driver gateway está seleccionado, solicita su aplicación
inmediata. Borrarlo detiene el túnel. Una respuesta 502 significa “guardado pero no aplicado”;
revísalo y vuelve a guardar cuando el sidecar responda. No se permiten hooks ejecutables.
`Table=off` mantiene split routing y no se aplica el DNS del archivo a los clientes.
El modo fail-closed impide salida por la tabla principal para destinos seleccionados cuando
no existe ruta por el túnel. Si el túnel existe pero no transporta, esos paquetes fallan en
el túnel; la salud muestra presencia y antigüedad del handshake, sin prometer conectividad
extremo a extremo con cada destino.

## Operación y desarrollo

- [Operación, seguridad y actualización](docs/OPERATIONS.md)
- [Arquitectura y contratos actuales](docs/ARCHITECTURE.md)
- [Alcance y criterios del MVP](docs/PROJECT.md)
- [Fases y evidencias](docs/PHASES.md), [roadmap](docs/ROADMAP.md), [decisiones](docs/DECISIONS.md)

```sh
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv run pytest --basetemp=/tmp/tebypass-tests
```

Las dependencias se fijan en `uv.lock`; Docker consume el export con hashes
`requirements.lock.txt`. Alpine y CSS se sirven localmente, sin Node en runtime.
Regenerar CSS tras cambiar clases:

```sh
npx --yes --package tailwindcss@3.4.17 tailwindcss -i app/static/input.css -o app/static/app.css --content app/templates/index.html --minify
uv export --no-dev --no-emit-project --format requirements-txt -o requirements.lock.txt
```

Ejecutar **un único worker** de control. Un proxy, DNS o la conectividad real de clientes
pueden introducir limitaciones ajenas al motor. El gateway funciona en Linux, no directamente
en el kernel de macOS/Windows.

## Créditos y licencia

Gracias a [jcastro/stopliga](https://github.com/jcastro/stopliga) por la idea y a la comunidad **HayAHora** por mantener las listas abiertas.
[Dependencias y atribuciones](THIRD_PARTY_NOTICES.md). TeByPass: AGPL-3.0-or-later.
El uso debe respetar la legislación y las condiciones de los servicios de red aplicables.
