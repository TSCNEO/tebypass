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
- La verificación final del contenedor Linux, UI, despliegue y política UniFi se registra
  al cerrar la entrega. No se almacena configuración ni infraestructura privada en Git.

## Límites de aceptación

Un MVP funcional no certifica todos los modelos/firmwares. Los escenarios de Omada,
OPNsense y MikroTik usan mocks/contratos y backend upstream; la evidencia física disponible
es UniFi. El test kernel del gateway es aislado, sin afirmar transporte por un proveedor
WireGuard real. IPv6 de gateway, sidecar remoto, políticas múltiples y formatos UniFi legacy
quedan fuera del alcance declarado del MVP, en ROADMAP.md.
