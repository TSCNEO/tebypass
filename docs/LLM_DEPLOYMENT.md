# Instalación asistida por LLM

Esta guía describe el MVP actual. El agente ejecuta los comandos en el servidor que
aloja el checkout Docker Compose. No debe pedir al usuario que identifique IPs internas
del proxy ni pegar contraseñas en el chat. No añadir datos del entorno a Git.

## Acceso web y reverse proxy

1. Inspeccionar el checkout, versión y estado de Compose. Conservar los volúmenes y
   configuración existentes. Actualizar y construir la imagen según OPERATIONS.md antes
   de usar el asistente; versiones antiguas de `/healthz` no ofrecen el diagnóstico.
2. Obtener la URL que usa el usuario para abrir el panel, o leerla de la configuración
   existente del proxy. No inventarla. El proxy debe apuntar al puerto interno 9384,
   conservar Host y enviar X-Forwarded-Proto; certificado TLS válido.
3. Ejecutar `python3 scripts/setup_proxy.py 'https://tebypass.example.org'`, sustituyendo
   la URL de documentación por la real. La salida JSON es el contrato de automatización.
4. Si devuelve `ready`, no cambiar nada. Si devuelve `needs_configuration` y el usuario
   ha autorizado configurar el despliegue, repetir con `--apply`. `applied` acredita que
   la aplicación reconoce HTTPS después del cambio. Es idempotente: repetir no recrea
   un servicio ya correcto. Nunca sustituir un error por confianza global `*`.
5. En `error`, leer el mensaje y revisar URL, TLS, acceso Docker o logs de acceso. El
   asistente restaura el archivo previo ante fallo de aplicación/verificación y avisa si
   tampoco puede recrear el servicio anterior. No anunciar éxito sin comprobarlo.
6. Tras configurar, iniciar sesión de nuevo para recibir una cookie Secure. Validar
   lectura y guardado vacío de ajustes autenticados; comprobar que un origen ajeno
   recibe 403. No cambiar el backend seleccionado para probar el acceso.

El asistente usa Python estándar >= 3.10 en el host. No necesita paquetes pip ni leer
el token administrativo. Crea una copia privada `.env.before-proxy-setup`; no mostrarla
ni subirla al repositorio. Servicios personalizados: ver `--help`.

## Configuración operativa

La clave administrativa se genera en `/data/admin_token`. El administrador puede
consultarla localmente con `docker compose exec tebypass cat /data/admin_token` e
introducirla en login. Un agente debe capturarla en memoria para llamadas API, sin
mostrarla en salidas ni guardarla en documentación. La API admite Bearer.

Consultar `/openapi.json` con autenticación para los contratos vigentes. Leer ajustes
antes de modificarlos, elegir driver y probar conexión. Mock solo simula; no acreditar
conectividad real con él. La VPN y las credenciales del router no se pueden inventar.
La fuente inicial está pausada hasta seleccionar una salida válida.

Para gateway virtual seguir [OPERATIONS.md](OPERATIONS.md#gateway-virtual-despliegue-en-la-lan):
inspeccionar interfaz, LAN y router, comprobar dirección libre, fijar MAC y recordar la
reserva DHCP. Estos parámetros pertenecen al entorno y requieren evidencia, no valores
predeterminados ficticios. Subir WireGuard desde el panel, probar salud del driver y
validar reenvío de un cliente. La prueba aislada del repositorio valida el motor de red,
pero no certifica Wi-Fi, DHCP o DNS del dispositivo del usuario.
