# Decisiones vigentes

Este archivo conserva únicamente decisiones aplicables al código actual. Cuando una
se sustituye, actualizar su descripción y conservar el motivo histórico en Git o en un
archivo cerrado. No acumular versiones contradictorias como reglas simultáneas.

## Separación de responsabilidades

Core orquesta; drivers traducen políticas sin BD ni API entrante; servicios atienden
HTTP; gateway posee nftables y WireGuard. Un solo worker y escritor de reconciliación.
Esto limita dependencias de hardware y permite probar cada frontera.

## Estado completo y observación

Una política de salida por instalación. Aplicar y observar estado remoto antes de
confirmar éxito; estado desconocido no equivale a conjunto vacío. Pausa/purga deben
retirar o desactivar lo gestionado y comprobarlo. UniFi administra ALL_CLIENTS.
Cambios de identidad retiran primero la política anterior; fallo de retirada rechaza
el cambio. Router y SQLite no forman una transacción distribuida.

## Persistencia y contribuciones

SQLite asíncrono con WAL, claves persistentes y secretos cifrados. Los destinos de un
feed conservan identidad y retención DNS si siguen presentes. El error de una fuente
conserva snapshot y muestra degradación. Manuales y feeds habilitados contribuyen a la
misma política; señal/horario comunitarios no deshabilitan destinos manuales.
La retención efectiva de una resolución es max(TTL sintético, ventana configurada).

## Capacidades e integración local

Capacidades declaradas en drivers/capabilities validan ingesta y cambio de driver.
Gateway, MikroTik y Omada son IPv4. No permitir combinaciones incompatibles para luego
fallar al aplicar todo el conjunto. Gateway solo por socket Unix y volumen compartido
con la aplicación; no hay sidecar remoto ni acceso Docker desde el panel.

## WireGuard y confirmación de protección

El sidecar comprueba regla de marca/tabla y ruta de protección; la aplicación exige ACK
verificado y política coincidente. Aplicación y sidecar se actualizan juntos.
Cada reemplazo guarda última configuración aplicada (0600); fallo intenta restaurar
archivo, túnel y ruta, y comunica si falla también el rollback. Una candidata guardada
no acredita que esté aplicada ni que haya conectividad de clientes.

## Sesiones y despliegue

Credencial administrativa local; API Bearer y sesión web firmada con origen validado.
El proxy conserva Host; las sesiones no requieren confianza global de cabeceras.
Distribución con imágenes y Compose fijados a versión, assets locales y sin Node en runtime.
Validar descarga anónima por separado de una CI autenticada antes de publicar instalación pública.
