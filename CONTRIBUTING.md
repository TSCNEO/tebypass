# Desarrollo

Leer [AGENTS.md](AGENTS.md) y los [contratos de arquitectura](docs/ARCHITECTURE.md).
Mantener core, drivers, API y gateway separados. Los drivers no acceden a BD ni HTTP
entrante. Usar únicamente direcciones y dominios de documentación en ejemplos y tests.
Nunca adjuntar datos de instalaciones reales a commits, logs de incidencias o fixtures.

## Entorno y comprobaciones

Python >= 3.12 y uv. Las dependencias están fijadas en `uv.lock`.

```sh
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy app tests
uv run pytest --basetemp=/tmp/tebypass-tests
```

Compilar con el override de desarrollo descrito en [Instalación](docs/INSTALLATION.md).
Ejecutar un único worker. Node no forma parte del runtime. Para regenerar CSS y el
export de dependencias después de cambios pertinentes:

```sh
npx --yes --package tailwindcss@3.4.17 tailwindcss -i app/static/input.css -o app/static/app.css --content app/templates/index.html --minify
uv export --no-dev --no-emit-project --format requirements-txt -o requirements.lock.txt
```

Las pruebas no requieren routers físicos; mocks y contratos no certifican hardware.
La [verificación Linux del gateway](docs/OPERATIONS.md#verificación-linux-aislada)
requiere Docker y capacidades de red en un entorno aislado.

## Entregas

Cambios mayores en rama descriptiva, commits Conventional Commits y comprobaciones
completas antes de integrar. Documentar cambios de API/modelos/arquitectura y decisiones
no triviales. [Publicación](docs/RELEASING.md) es un procedimiento del mantenedor,
separado de la instalación de usuarios.
