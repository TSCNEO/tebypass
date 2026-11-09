# Fases de cierre del MVP 0.2

## Implementación

1. **Control fiable:** apagado verificado, errores explícitos y escritores serializados.
2. **Fuente comunitaria:** snapshot global/destinos separados, lógica fijada de stopliga,
   frescura, límites, ETag y preservación del último válido con error visible.
3. **Motor operativo:** retención DNS persistida, purga de expirados, unión normalizada,
   CRUD reactivo, intervalo 0 reactivable y transición de configuración controlada.
4. **UniFi/gateway:** política completa, observación y comprobación posterior; WireGuard
   validado/aplicado/retirado por el sidecar y ruta de descarte fail-closed.
5. **Routers:** Omada/OPNsense con adaptador de backend upstream fijado y MikroTik REST con
   reglas hacia tabla preparada. UI y credenciales cifradas. No son stubs ni emuladores.
6. **Producto y entrega:** autenticación, salud detallada, assets locales, dependencias con
   hashes, build versionado, horario comunitario y webhook opcionales; documentación sustituida.

## Evidencias

- Fase 1 se desplegó y validó con 83 tests más sondas HTTP/conectividad UniFi.
- El cierre del MVP amplía los tests de señal global, retención real en el motor,
  autenticación/CSRF, validación WireGuard, reglas RouterOS, adaptadores y horarios/webhook.
- La suite externa de stopliga para Omada e integración se ejecutó sobre la revisión fijada:
  **66 pruebas correctas**. No se copiaron sus destinos reales a fixtures del proyecto.
- Suite final: **103 pruebas correctas**, Ruff y formato conformes, Mypy sin errores en
  `app tests`; verificación de tipos del gateway también correcta.
- Imagen Linux: arranque real del lifespan, autenticación, assets y ciclo API completo de
  alta/pausa/reactivación/borrado/logout sobre base aislada.
- Gateway Linux: comandos del controlador contra nftables real, descarte seleccionado,
  ruta normal y fail-open explícito; creación/retirada de WireGuard con claves efímeras;
  arranque Docker completo y restauración del checkpoint después de reiniciar.
- UniFi real: política temporal de documentación creada, activada, desactivada y eliminada,
  verificando el resultado remoto. Se detectó y corrigió que UniFi exige conservar miembros
  al desactivar: una lista vacía devuelve HTTP 400 en el firmware probado.
- Navegador local: acceso, carga sin CDN, campos Omada y alta/pausa manual comprobados.
- **Desplegado en el entorno autorizado**, build `c0e1ef1`, versión 0.2.0. Ambos contenedores
  running/healthy. Feed habilitado, señal global inactiva, ningún override manual y **cero
  prefijos efectivos** tras sincronización verificada. API y sonda UniFi correctas;
  lecturas privadas anónimas rechazadas y logout revoca la sesión.
- Copia consistente de SQLite y claves conservada, junto con imágenes anteriores para
  rollback. No se almacena configuración ni infraestructura privada en Git.

## Límites de aceptación

Un MVP funcional no certifica todos los modelos/firmwares. Los escenarios de Omada,
OPNsense y MikroTik usan mocks/contratos y backend upstream; la evidencia física disponible
es UniFi. El test kernel del gateway es aislado, sin afirmar transporte por un proveedor
WireGuard real. IPv6 de gateway, sidecar remoto, políticas múltiples y formatos UniFi legacy
quedan fuera del alcance declarado del MVP, en ROADMAP.md.

## Diagnóstico posterior del gateway desde clientes

Se amplió la prueba de salida del propio contenedor con una prueba de **reenvío**:
cliente → gateway → endpoint DNS/HTTP, comprobando NAT de retorno y comportamiento
fail-closed/recuperación. Se ejecutó correctamente sobre la imagen desplegada y se
incorporó a CI. La red de laboratorio utiliza exclusivamente rangos de documentación.

En el entorno autorizado se verificaron también DNS y HTTPS directo, cadena forward
abierta y una conexión HTTPS marcada por WireGuard que obtuvo HTTP 200 y estableció
handshake. Estas evidencias no sustituyen la revisión de IP, máscara, DNS y aislamiento
Wi-Fi del móvil concreto que reportó el problema.

La prueba de reenvío se amplió con un peer WireGuard real aislado: el cliente obtiene
DNS/HTTP por el túnel y completa subida y descarga de 256 KiB. Al eliminar la interfaz
del túnel, el destino seleccionado queda bloqueado; al retirar la selección recupera
la salida normal. Los peers usan claves efímeras independientes de cualquier proveedor.
Esta ampliación pasó sobre la imagen desplegada y se ejecuta mediante el mismo job de CI.

## Acceso mediante Traefik HTTPS

Se reprodujo el rechazo del guardado por origen distinto al terminar TLS en Traefik:
HTTP interno y HTTPS del navegador no coincidían. Se añadió confianza explícita de la
conexión del proxy en Uvicorn y normalización de origen/puertos, conservando el rechazo
de orígenes ajenos. La configuración real del proxy permanece solo en el host de despliegue.

Validado con **105 tests** y controles de calidad. Tras desplegar `6f1aa1a`, por el acceso
HTTPS real: login 200 con cookie Secure, guardado 200 y origen ajeno 403. El backend quedó
seleccionado como `virtual_gateway` en el entorno autorizado.
