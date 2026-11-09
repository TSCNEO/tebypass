# Dependencias y atribuciones

- **stopliga**, Jonatan / [jcastro](https://github.com/jcastro/stopliga), revisión
  `2ef9357702044367ff81b1bec51d77862c725764`, versión declarada 0.1.31, licencia MIT
  declarada en sus metadatos. Se consume sin modificar como dependencia fijada; aporta
  la interpretación de la señal comunitaria, los clientes Omada/OPNsense y sus garantías
  de verificación/rollback. TeByPass mantiene su propia API, persistencia y orquestación.
- **Alpine.js 3.14.3**, licencia MIT, distribuido localmente en `app/static/vendor/`.
  El texto de licencia acompaña al fichero en `alpine.LICENSE`.
- **Tailwind CSS 3.4.17**, licencia MIT. CSS generado y empaquetado en `app/static/app.css`.
  Node/npm solo se usan al regenerar assets durante desarrollo; no están en las imágenes.

Las dependencias Python y sus hashes están fijados en `uv.lock` y el export
`requirements.lock.txt`. Las licencias individuales permanecen en los paquetes instalados.
