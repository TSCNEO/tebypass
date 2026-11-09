# Alcance actual del MVP

TeByPass debe mantener una política de desvío selectivo hacia una salida existente,
activando la contribución comunitaria según bloqueos reales y conservando destinos
manuales independientes. No crea cuentas VPN ni deduce bloqueos a partir de partidos.

## Criterios funcionales

1. Fuente global inactiva → contribución comunitaria inactiva; manuales no afectados.
2. Fuente inválida/obsoleta/inaccesible → último estado válido preservado y error visible.
3. Cambio DNS/fallo temporal → IP anterior retenida hasta su vencimiento; pausa/borrado
   del destino retira su contribución inmediatamente.
4. Cada ciclo observa y comprueba el router; configuración/ausencia de transporte no
   bastan para declarar éxito. Ningún driver operativo emula escrituras.
5. Cambiar destino, pausa, borrado, feed y ajustes provoca reconciliación. Intervalo 0
   pausa el automático y puede reactivarse sin reinicio.
6. Toda administración requiere credencial. Secretos se enmascaran y cifran; el runtime
   no depende de CDN ni de Node.
7. El gateway valida/aplica/retira WireGuard y corta el tráfico seleccionado sin ruta de
   túnel si fail-closed está activo.

## Capacidad y límites

Una política de salida, un driver y un worker por instalación. Omada y OPNsense reutilizan
el backend de stopliga; MikroTik usa una tabla FIB previamente preparada; UniFi soporta V2.
Gateway solo IPv4 y socket/volumen local. No se afirma compatibilidad certificada en todos
los modelos o firmwares; los requisitos por router están en README.

Horario comunitario y webhook HTTPS son opcionales. No hay aplicaciones de notificación
específicas, editor de políticas múltiples, transporte remoto del sidecar ni observación
continua del tráfico de clientes. Una IP caducada puede cortar conexiones antiguas que
la aplicación no ve. Ni el estado de una regla ni la presencia de WireGuard prueban que
un servicio externo sea accesible desde un cliente: esa prueba corresponde a operación.

La auditoría de noviembre permanece como documento histórico. Las afirmaciones de
completitud del roadmap inicial han sido reemplazadas por las evidencias de PHASES.md.
