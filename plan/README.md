# Planes de desarrollo

Contenido de trabajo para tareas de planificación o implementación explícitamente
encargadas. No describe funcionalidades disponibles ni sirve para instalar TeByPass.
No debe consultarse durante un despliegue.

## Pendiente

- [Continuidad y pools VPN](VPN_POOL_PLAN.md). Estado: propuesto, sin implementar.
  Prioridad acordada el 2026-11-10: principal/reserva ante fallo; selección por servicio
  y reparto posteriores. No se incluye en la versión 0.2.1.

## Ciclo de vida obligatorio

1. Cada plan indica estado, fecha, alcance y criterios de aceptación. Registrar aquí
   solo trabajo pendiente o en curso, con enlaces a su plan.
2. Antes de implementar, contrastarlo con código y documentación vigente; no asumir que
   un plan antiguo continúa siendo correcto. Sustituir versiones duplicadas del mismo plan.
3. Al terminar, verificar aceptación y actualizar docs de uso y maintenance. Retirar el
   plan de pendientes en la misma entrega; no dejarlo activo con todas las tareas marcadas.
4. Eliminar borradores redundantes: Git conserva su historia. Cuando importe explicar
   cuándo y por qué se actuó, mover el documento a done con estado cerrado, fecha,
   resultado y commit/release, evitando duplicar instrucciones vigentes.
5. Plan cancelado/sustituido sigue el mismo cierre, indicando el motivo. Un documento
   cerrado no se reactiva: crear un plan nuevo contrastado con el estado actual.

## Histórico

[done](done/README.md) se conserva solo para investigación histórica solicitada
expresamente. No leerlo por defecto, ni usarlo como fuente de requisitos, instrucciones,
capacidades presentes, trabajo pendiente o configuración. Una referencia histórica no
prevalece sobre el código ni sobre docs/maintenance vigentes.
