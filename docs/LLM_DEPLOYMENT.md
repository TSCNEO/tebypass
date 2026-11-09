# Instalación asistida por LLM

Esta guía describe el MVP actual. El agente ejecuta los comandos en el servidor que
aloja el checkout Docker Compose. No debe pedir al usuario que identifique IPs internas
del proxy ni pegar contraseñas en el chat. No añadir datos del entorno a Git.

## Acceso web y reverse proxy

1. Inspeccionar checkout y estado de Compose. Conservar volúmenes y configuración
   existentes. En instalación de release, descargar el Compose adjunto y ejecutar
   pull/up según OPERATIONS.md; no clonar ni compilar. Solo para desarrollo se usa
   docker-compose.build.yml junto al Compose base.
2. Si se usa un proxy, comprobar que apunta al puerto interno 9384 y conserva Host.
   Configurar certificado y ruta en el proxy si forman parte del despliegue autorizado.
3. Abrir el panel e iniciar sesión normalmente. El formulario reconoce la URL pública
   automáticamente; no pedir IPs internas, editar `.env` ni ejecutar setup_proxy.py
   para habilitar el guardado. No cambiar el backend seleccionado al probar acceso.
4. Validar login y guardado autenticado vacío, así como rechazo 403 de un origen ajeno.
   HTTPS debe emitir cookie Secure incluso con transporte HTTP interno sin confianza
   de proxy. Si la sesión es antigua, indicar que debe iniciar sesión una vez más.
5. Si se automatiza login HTTP con cookie en lugar de navegador, enviar `browser_origin`
   en JSON junto a `token`, y cabecera Origin, ambos con la URL pública. Host debe
   conservar esa autoridad. Para llamadas de agente se recomienda API Bearer.
6. Si el login rechaza el origen, revisar Host en el proxy, no habilitar confianza global.

El asistente `scripts/setup_proxy.py` queda como herramienta avanzada opcional para los
metadatos reenviados de Uvicorn. No es requisito de uso de la web. Su modo diagnóstico
no modifica nada; `--apply` configura con copia privada y restauración según OPERATIONS.md.

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
