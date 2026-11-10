# Roadmap — estado del MVP 0.2

La lista original de tareas marcaba capacidades incompletas como terminadas. Este documento
se reemplaza por el alcance verificable; el detalle de evidencia vive en PHASES.md.

## Implementado

- Señal global comunitaria de stopliga, snapshots validados, frescura, ISP y límites.
- Destinos manuales/carpetas, retención DNS persistida, borrados y pausas reactivos.
- Política completa y escritores serializados; apagado y errores explícitos.
- UniFi V2, Omada/OPNsense mediante backend fijado, reglas REST MikroTik y gateway Unix.
- WireGuard validado, aplicación/retirada y política kernel fail-closed.
- Login/API autenticados, secretos cifrados, salud detallada, assets locales, builds fijados.
- Horarios comunitarios y webhook de transiciones opcionales.

## Validación y cierre

MVP integrado en main y desplegado. Las pruebas locales, kernel Linux, despliegue y sonda
operativa están registradas en PHASES.md.
“Implementado” no significa probado en hardware de todos los fabricantes.

## Evolución posterior al MVP

- Matriz de laboratorio por modelo/firmware para Omada, OPNsense y MikroTik.
- Formatos UniFi legacy y listas vinculadas adicionales de stopliga.
- [Continuidad y pools VPN](VPN_POOL_PLAN.md): principal/reserva, sondas por salida y
  fases posteriores de selección por servicio y reparto. Propuesta sin implementar.
- Múltiples políticas por instalación y roles de administración.
- Sidecar remoto autenticado e IPv6 de extremo a extremo.
- Adaptadores de notificaciones específicos, métricas Prometheus y automatización de backups.
