# Uso del panel

Primero completar [Instalación](INSTALLATION.md). [Configuración](CONFIGURATION.md)
describe todos los campos y sus valores; [API](API.md) cubre las operaciones adicionales.

## Destinos manuales y listas

1. Pulsar **+ Añadir**. Introducir FQDN, IP o CIDR; elegir lista opcional, activo/pausado
   y nota. Guardar. Ejemplos de sintaxis: `edge-node-01.example.net`, `198.51.100.7`,
   `198.51.100.0/24`; son reservados de documentación, no destinos de prueba públicos.
2. No introducir URLs con protocolo/ruta ni comodines. Un dominio no incorpora
   automáticamente todos sus subdominios. IP suelta se normaliza a /32 o /128;
   CIDRs se normalizan a la red correspondiente.
3. Editar cambia destino, nota, habilitación o lista. **Resolver ahora** (↻) vuelve a
   consultar DNS para el FQDN. IP/CIDR directo no necesita resolución.
4. El interruptor de una fila pausa solo esa contribución; **Borrar** elimina el registro.
   Si otro destino/feed requiere la misma IP, esa ruta puede seguir activa.
5. **Importar varios** acepta un destino por línea y lista opcional. Omite líneas vacías
   y comentarios que empiezan por `#`. El panel informa creados y duplicados omitidos;
   la respuesta API también contiene las líneas inválidas.
   El ejemplo siguiente ilustra formato, no conectividad:

```text
# Destinos de documentación
edge-node-01.example.net
198.51.100.7
203.0.113.0/24
```

**+ Nueva lista** crea nombre y color. Las listas/carpetas solo organizan: no seleccionan
VPNs distintas ni limitan clientes. Borrar una lista conserva sus destinos sin asignación.
Renombrar/recolorear una lista está disponible mediante PATCH de API; el panel actual
ofrece creación y borrado. El buscador filtra por valor, nota o lista; no modifica rutas.

Las IPs de FQDN se resuelven desde el servidor de TeByPass, no desde cada TV/PC. DNS
geográfico, caché o DNS cifrado del cliente pueden producir otras IPs: comparar resultados
si no coincide la ruta. La retención conserva IPs previas hasta su vencimiento, incluso
tras reinicio. Puede desviar otros servicios alojados en una IP compartida: es L3,
no una inspección de URLs. Las rutas agregadas pueden ser menos que los destinos de la tabla.

## Lista comunitaria y otras fuentes

El interruptor comunitario habilita/pausa ese feed, no todos los destinos.
**Actualizar lista** solicita descarga; no equivale a habilitar una fuente pausada.
**Vaciar** elimina sus destinos y la pausa para impedir relleno inmediato. Las fuentes
pueden reponer sus registros al descargarse: editar manualmente un registro importado
no constituye una exclusión persistente. Usar un destino manual para contribuciones propias.

Estados: PAUSADA, SIN CONFIRMAR, BLOQUEO ACTIVO o SIN BLOQUEOS. Error de descarga, esquema,
frescura o límite conserva el snapshot válido anterior y muestra degradación. La fuente
no se convierte en vacía por un error. El modo forzado usa incidencias recientes aunque
la señal global sea inactiva; mantiene frescura, habilitación y horario.

Registrar múltiples fuentes o eliminarlas se hace por [API](API.md); el panel principal
presenta una fuente comunitaria, no un gestor completo de feeds. Crear fuente sin
`enabled` la habilita por defecto: preparar salida antes o enviar `enabled: false`.

Formatos disponibles en `format`:

- `plaintext`: destino por línea, vacíos y comentarios `#` ignorados.
- `json`: array de cadenas/objetos o diccionario con listas `domains`, `hosts`, `ips`,
  `cidrs`. Objetos con `value`, `target`, `domain` o `ip`, y `description` opcional.
- `hayahora` / `stopliga`: adaptador comunitario con señal, antigüedad e incidencias;
  no es un formato JSON genérico. JSON con estructura comunitaria también se detecta.

Ejemplo JSON genérico:

```json
{"domains": ["edge-node-01.example.net"], "cidrs": ["198.51.100.0/24"]}
```

URL HTTP(S) sin credenciales; descargar desde la URL final, sin depender de redirecciones.
Máximo 16 MiB por respuesta, timeout 30 s y límite de destinos configurable. JSON inválido
no cae a texto/HTML. Documento inválido se rechaza completo; una lista vacía válida sí
sustituye la anterior. HTTP 304 no rejuvenece la fecha original comunitaria.

## Sincronizar, pausar y retirar

- **Sincronizar** en el panel solicita un ciclo forzado: refresco de fuentes habilitadas,
  DNS, unión/agregación de destinos y
  política del driver. Las escrituras también solicitan reconciliación; revisar último
  resultado porque guardar no garantiza éxito del router.
- **Enrutamiento activo** en Ajustes → Router es el interruptor global. Al desactivarlo,
  solicita retirar lo gestionado sin borrar destinos ni credenciales.
- **Purgar regla del router / Eliminar regla de UniFi** retira la política y deja
  enrutamiento global pausado. Omada/OPNsense pueden conservar grupos/alias reutilizables.
  No borra reglas ajenas. Fallo remoto se informa; no asumir retirada confirmada.
- Detener el contenedor no es purgar: una política puede persistir en el router. Antes
  de desinstalar, pausar/purgar y verificar. Con gateway virtual restaurar también las
  puertas de enlace de clientes antes de retirarlo. Conservar el volumen para recuperación.

La pausa global, la pausa de feed y deshabilitar un destino tienen alcances distintos.
Manual y feeds comparten una política; una IP se retira cuando ninguna contribución
habilitada la necesita. Una carpeta no es una contribución con interruptor propio.

## Horario, alertas y diagnóstico

[Referencia de motor](CONFIGURATION.md#motor-y-fuentes) para días, franja, zona IANA,
intervalo, retención, filtro de operador y webhook. Ejemplo: `mon,tue,wed,thu,fri`,
`18:00-23:00`, `Europe/Madrid` con sincronización positiva. Fuera de horario no
contribuye la fuente comunitaria; destinos manuales permanecen habilitados.

El panel distingue destinos, IPs resueltas y rutas activas; no son contadores equivalentes.
Último error/último intento y estado del driver importan más que un contenedor healthy.
`total_active = -1` significa estado remoto desconocido. **Probar conexión en el panel guarda primero los valores del formulario**, ejecuta
la sonda y, si está saludable, solicita sincronización. Por tanto puede cambiar políticas;
no es una consulta sin efectos. El endpoint API test-router por separado usa los valores
ya guardados. Revisar driver, salida, reglas y
tráfico desde un cliente; un handshake WireGuard no acredita todas las aplicaciones.

Para WireGuard, seguir [la carga desde el panel](../README.md#cargar-la-configuración-wireguard)
y [diagnóstico de cliente](OPERATIONS.md#un-cliente-usa-el-gateway-pero-no-navega).
Para acceso, HTTPS, claves, copias, actualización y reversión consultar [Operación](OPERATIONS.md).
