"""Create a version-pinned installation bundle without repository/runtime state."""

import argparse
import hashlib
import re
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


def prepare(version: str, output: Path, project: Path = PROJECT_DIR) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", version):
        raise ValueError("Use X.Y.Z or X.Y.Z-rc.N/alpha.N/beta.N")
    output.mkdir(parents=True, exist_ok=True)
    compose = (project / "docker-compose.yml").read_text()
    compose = compose.replace(
        "${TEBYPASS_IMAGE_TAG:-latest}", "${TEBYPASS_IMAGE_TAG:-" + version + "}"
    )
    (output / "docker-compose.yml").write_text(compose)
    shutil.copyfile(project / ".env.example", output / "env.example")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(project / name, output / name)
    (output / "INSTALL.md").write_text(
        f"# TeByPass {version}\n\n"
        "Requiere Docker Engine y Compose v2 en Linux.\n\n"
        "```sh\ndocker compose up -d --wait\n"
        "docker compose exec tebypass cat /data/admin_token\n```\n\n"
        "Abre el puerto 9384 e introduce la clave. Configura y prueba el router desde Ajustes.\n"
        "El feed inicial está pausado. No se necesita Git, Python ni compilar imágenes.\n\n"
        "Gateway virtual: Linux, macvlan, IP propia, MAC fija y reserva DHCP; es opcional.\n"
        f"Guía: https://github.com/TSCNEO/tebypass/blob/v{version}/docs/OPERATIONS.md\n\n"
        f"Código fuente: https://github.com/TSCNEO/tebypass/tree/v{version}\n"
    )
    names = [
        "docker-compose.yml",
        "env.example",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "INSTALL.md",
    ]
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--output", type=Path, default=Path("dist/release"))
    args = parser.parse_args()
    prepare(args.version, args.output)


if __name__ == "__main__":
    main()
