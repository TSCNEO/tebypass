# Auditoría de comportamiento funcional

> Informe de la base indicada. Los seis hallazgos se corrigen en 0.2.1; consultar
> la [auditoría posterior](POST-FIX-AUDIT-0.2.1.md) para resultados y límites actuales.

Fecha: 2026-11-10. Base de producto: `f79c36f7e61ad427b12c0cfa3152bcd225682388`.
Alcance: contrastar los criterios de PROJECT con ejecución de servicios, SQLite,
API, ciclo de vida y drivers bajo fallos controlados. No es una revisión de estilo.

## Dictamen

El recorrido básico del motor funciona en las condiciones verificadas, pero no se
puede afirmar que todas las garantías previstas se cumplan. Hay seis casos de aceptación
reproducidos que fallan, detallados abajo. Antes de declarar la entrega funcionalmente
cerrada deben corregirse y completarse las pruebas de tráfico/router indicadas.

La auditoría añade pruebas y corrige aislamiento de fixtures; no modifica el runtime
ni repara los defectos descritos. Los seis `xfail(strict=True)` representan deuda
funcional explícita, no seis éxitos. Ejecutar con `--runxfail` expone sus fallos reales.

## Fallos y capacidades incompletas

### F01 — P1: refrescar un feed borra retención DNS todavía vigente

Contrato: una IP resuelta se conserva hasta su vencimiento ante rotación/fallo DNS.
Reproducción: feed plaintext con un FQDN, resolver una IP con una hora de vigencia,
provocar fallo DNS y reconciliar. La IP se conserva. Refrescar el mismo feed con HTTP 200,
sin cambiar su contenido, y reconciliar: desaparece la ruta antes del vencimiento.

Causa: `app/core/feeds_sync.py:72` elimina todos los Target y crea otros IDs; la cascada
borra ResolvedIP. El motor no reutiliza las entradas antiguas de la caché para el nuevo
Target. Afecta feeds genéricos con FQDN, no demuestra un fallo del feed comunitario de IPs
ni de retención manual. Rotación puede retirar prematuramente IPs anteriores.

Corrección: actualizar por identidad normalizada, conservar Target/ResolvedIP para
entradas que siguen presentes y borrar solo las retiradas. Aceptación: mismo FQDN tras
refresh conserva IPs no vencidas incluso con fallo DNS y tras reinicio.

Prueba: `test_feed_refresh_preserves_unexpired_dns_retention`.

### F02 — P1: un destino IPv6 se acepta y después bloquea la política IPv4

Reproducción: seleccionar MikroTik, enviar `2001:db8::7/128` por POST targets. Se responde
201 y persiste. Reconciliar falla con `RouterOS MVP supports IPv4 only`, antes de enviar
la política al router. El controlador no puede aplicar el conjunto deseado mientras
incluya ese destino. Gateway también rechaza IPv6 en su transporte; no se ha verificado
ese recorrido completo con paquetes en esta sesión.

Causa: validación de destinos independiente de capacidades; la restricción de ajustes
solo comprueba resolve_ipv6, no CIDRs explícitos ni contenido de feeds.

Corrección: validar entradas y cambios de driver, incluida la descarga de fuentes,
contra capacidades declaradas. Rechazar con explicación o aislar destinos incompatibles
sin impedir aplicar los compatibles; evitar silencios o éxito ficticio.

Prueba: `test_ipv4_driver_rejects_unsupported_target_at_ingestion` demuestra aceptación
201 seguida de fallo real del motor con driver MikroTik, sin requerir un router.

### F03 — P1: cambiar WireGuard no recupera el túnel anterior si falla la activación

Reproducción controlada: archivo activo anterior válido, nuevo archivo validado y fallo
inyectado en wg-quick up. reload llama stop primero y sustituye el archivo activo;
tras el error no restaura la configuración anterior ni solicita volver a levantarla.

Causa: `gateway/wireguard_runtime.py:50` valida sintaxis antes de parar, pero no hace
rollback ante errores posteriores (activación o instalación de ruta). Una configuración
válida sintácticamente puede fallar al aplicarse. El 502 informa del fallo, no lo repara.

Impacto: actualización de VPN puede dejar sin servicio los destinos seleccionados;
requiere intervención. El fail-closed no ofrece continuidad. La reproducción comprueba
orquestación y archivo; no simula que haya habido un túnel kernel real en este host.

Corrección: conservar última configuración aplicada y restaurar túnel/ruta ante fallo;
mostrar fallo de la actualización y resultado de restauración por separado. Probar también
error durante instalación de ruta y fallo de la propia restauración.

Prueba: `test_wireguard_reload_restores_previous_config_on_activation_failure`.

### F04 — P1: UniFi puede confirmar destinos correctos con alcance de clientes incorrecto

Contrato anunciado: regla del driver para todos los clientes alcanzados por ALL_CLIENTS.
Reproducción con HTTP simulado: regla existente con target_devices restringido, aplicar
un destino y comprobar respuesta. El driver devuelve éxito y conserva ese alcance
restringido. Las verificaciones solo comparan enabled, IPs y network_id.

Causa: `app/drivers/unifi.py:270` copia la regla existente; ALL_CLIENTS solo se fija al
crear. Cambios externos o una regla previa con el mismo nombre pueden provocar que
parte de los clientes no use la salida aunque la sincronización aparezca correcta.
La forma de dispositivo de la prueba es ilustrativa; no certifica el esquema de cada
firmware ni reproduce paquetes en hardware.

Corrección: definir y comprobar la política completa, incluido alcance de clientes.
Para una regla gestionada íntegramente, reconciliar ALL_CLIENTS; si se decide respetar
alcance manual, declararlo como contrato y mostrarlo explícitamente en el panel.

Prueba: `test_unifi_reconciles_managed_client_scope`.

### F05 — P1: falta de confirmación de fail-closed puede terminar en éxito

Reproducción: el comando policy del gateway devuelve None (timeout/conexión fallida),
y las siguientes operaciones de lista/sync funcionan. apply_policy termina con éxito.

Causa: `app/drivers/virtual_gateway.py:125` rechaza respuesta explícita negativa, pero
no ausencia de respuesta. Verifica miembros del set, no la política fail-closed aplicada.

Impacto: la aplicación no puede garantizar que el ajuste elegido haya sido aplicado.
No prueba una fuga de tráfico: el comando puede haberse aplicado y haberse perdido su
respuesta, o puede no haberse aplicado. Precisamente falta distinguir ambos estados.

Corrección: ausencia de ACK debe fallar/degradar; además observar política efectiva
para verificar su convergencia. Aceptación: no confirmar éxito ante ACK perdido sin
una observación posterior que demuestre la política elegida.

Prueba: `test_gateway_requires_fail_closed_policy_acknowledgement`.

### F06 — P1: Omada permite activar IPv6 que su backend no soporta

Reproducción: guardar router_driver=omada y resolve_ipv6=true se acepta. La función
real `_collapse_destinations` de la dependencia fijada rechaza un prefijo IPv6 con
UnsupportedRouteShapeError. El primer AAAA que contribuya puede impedir la política.

Causa: `app/core/settings_store.py:225` limita IPv6 para gateway/MikroTik, omitiendo
Omada. La documentación anterior tampoco reflejaba esta limitación del backend.

Corrección: incluir capacidades de Omada en validación, ingesta y documentación.
Forma parte del mismo problema de capacidades de F02, con reproducción independiente.
No debe asumirse soporte IPv6 por el mero hecho de que el panel permita activarlo.

Prueba: `test_omada_rejects_ipv6_configuration_before_backend_failure`.

## Qué sí está demostrado y qué falta

| Recorrido / garantía | Evidencia disponible | Límite / siguiente aceptación |
|---|---|---|
| Primera instalación, fuente pausada, target, sincronización, reinicio y purga | Nuevo test con lifespan real, HTTP ASGI, SQLite y Mock | No enruta paquetes; añadir instalación Docker descargada en Linux |
| Unión manual/feed, pausa de feed, pausa global | Nuevo test de unión y apagado | Confirmar mismos efectos en cada dispositivo real |
| Señal global inactiva y error de fuente | Suite existente: manual preservado, snapshot previo y error | Añadir secuencias largas, reintentos y cambios simultáneos |
| DNS manual conservado en fallo y retirado al deshabilitar | Suite de retención existente | F01 falla para feeds; faltan rotación y reinicio con DNS real controlado |
| Cambios/errores remotos no simulados como éxito | Suite de drivers y apagado | F04/F05 muestran verificación incompleta de política |
| UniFi V2 | Pruebas HTTP de contrato | Falta aceptación actual con clientes y VPN reales; modelo/firmware importan |
| Omada/OPNsense | Adaptador probado con observación y backend simulados | No acreditan ciclo completo Open API → regla → tráfico; ampliar pruebas del adaptador con HTTP real simulado |
| MikroTik | REST simulado, comprobación de reglas | No acredita FIB/ruta VPN/FastTrack ni tráfico real; F02 abierto |
| Gateway nft/WireGuard y tráfico de clientes | Scripts Linux existentes y CI preparada | No ejecutados en esta sesión: Docker daemon local no disponible |
| WireGuard subir/aplicar/borrar | API y normalización existentes, F03 nuevo | Añadir reemplazo fallido, rollback y stop/restart con kernel real |
| Horario y webhook | Suite: horario cerrado conserva manuales; deduplicación de transiciones | Faltan límites nocturnos/DST y recuperación del receptor en integración completa |
| Autenticación/proxy/persistencia | Suite de sesiones, orígenes, secretos y proxy | No acredita todos los proxies ni navegadores/firmwares |

Las funcionalidades excluidas del MVP (varias VPN por lista, selección de clientes desde
panel, sidecar remoto, observación continua de conexiones) no son fallos por sí mismas.
Sí falta evidencia de aceptación de los recorridos anunciados en cada router soportado.

## Orden de trabajo y criterio de cierre

1. F02/F06: capacidades coherentes desde ingesta hasta cambio de driver; impedir que
   entradas incompatibles inutilicen toda la política.
2. F01: preservar identidad/retención de destinos que siguen en un feed.
3. F05/F04: confirmar política completa (protección y alcance), no solo membresía de IPs.
4. F03: reemplazo de WireGuard con recuperación verificable.
5. Ejecutar scripts de red Linux y pruebas de cliente por driver con red de laboratorio,
   incluyendo activación, pausa, reinicio, caída del túnel, recuperación y tráfico no
   seleccionado. Conservar evidencia sanitizada de versiones y resultados.

Retirar cada xfail al corregirlo y exigir que su criterio de aceptación pase. Un XPASS
estricto obliga a revisar la prueba y actualizar el estado del hallazgo. Evitar declarar
cierre por un número total de tests si quedan estos casos o validaciones externas pendientes.

## Reproducir y resultado

```sh
uv run pytest --basetemp=/tmp/tebypass-functional-tests -rx
uv run pytest tests/test_functional_audit.py --runxfail --basetemp=/tmp/tebypass-functional-proof --tb=short
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
```

Suite con nuevas pruebas: 122 passed / 6 xfailed. Con --runxfail en el módulo de auditoría:
2 passed / 6 failed, en las aserciones de aceptación indicadas. Ruff/formato/Mypy verificados.
No se usan redes, credenciales ni inventario de producción. Se corrigió el orden de
limpieza de conftest: init_db podía recargar ajustes del test anterior después de restaurar
el snapshot, contaminando pruebas posteriores. Es un defecto del arnés de pruebas;
no se contabiliza entre los seis fallos del producto.
