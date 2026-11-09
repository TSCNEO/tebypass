"""Configure reverse-proxy trust from the public URL, without credentials or guessed IPs."""

import argparse
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_KEY = "TEBYPASS_TRUSTED_PROXIES"


class SetupError(Exception):
    pass


def command(args: list[str], project: Path) -> str:
    env = dict(os.environ)
    env.pop(ENV_KEY, None)
    result = subprocess.run(args, cwd=project, env=env, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise SetupError(
            "Docker no pudo completar la operación. Revisa el servicio y sus permisos."
        )
    return result.stdout + result.stderr


def probe(url: str) -> tuple[dict[str, Any], str]:
    nonce = uuid.uuid4().hex
    request = urllib.request.Request(url + "/healthz?setup_probe=" + nonce)
    with urllib.request.urlopen(request, timeout=5) as response:
        final = urlsplit(response.geturl())
        requested = urlsplit(url)
        if (final.scheme, final.netloc) != (requested.scheme, requested.netloc):
            raise SetupError("La URL redirige a otro origen. Usa la URL final del panel.")
        data: Any = json.load(response)
    if not isinstance(data, dict) or data.get("service") != "tebypass":
        raise SetupError("La URL no corresponde al servicio TeByPass.")
    if data.get("scheme") not in {"http", "https"}:
        raise SetupError("Actualiza primero la imagen de TeByPass para usar este asistente.")
    return data, nonce


def inspect_access(url: str, project: Path, container: str) -> dict[str, Any]:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SetupError("Indica la URL HTTP(S) del panel, sin credenciales ni parámetros.")
    health, nonce = probe(url)
    if health["scheme"] == parsed.scheme:
        return {"state": "ready", "changed": False, "message": "El acceso ya está configurado."}
    if parsed.scheme != "https" or health["scheme"] != "http":
        raise SetupError("La configuración del proxy no coincide con la URL indicada.")
    # A random marker proves which connection actually carried this public request.
    logs = command(["docker", "logs", "--since", "20s", "--tail", "2000", container], project)
    peers: set[str] = set()
    for line in logs.splitlines():
        if nonce not in line:
            continue
        match = re.search(r'INFO:\s+(.+?):\d+\s+-\s+"GET ', line)
        if match:
            try:
                peers.add(str(ipaddress.ip_address(match.group(1).strip("[]"))))
            except ValueError:
                continue
    if len(peers) != 1:
        raise SetupError(
            "No se pudo identificar la conexión del proxy. Revisa los logs de acceso y vuelve a ejecutar."
        )
    return {
        "state": "needs_configuration",
        "changed": False,
        "proxy_connection": peers.pop(),
        "message": "Se ha identificado el proxy. Añade --apply para configurar y verificar.",
    }


def write_private(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".proxy-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_access(
    url: str, project: Path, container: str, service: str, plan: dict[str, Any]
) -> dict[str, Any]:
    if plan["state"] == "ready":
        return plan
    path = project / ".env"
    original = path.read_text() if path.exists() else ""
    existed = path.exists()
    backup = project / ".env.before-proxy-setup"
    if not backup.exists():
        write_private(backup, original)
    existing = re.search(r"^" + ENV_KEY + r"=(.*)$", original, re.M)
    values = ["127.0.0.1", str(plan["proxy_connection"])]
    if existing:
        for value in existing.group(1).strip().strip("\"'").split(","):
            value = value.strip()
            if not value:
                continue
            if value == "*":
                raise SetupError(
                    "Retira la confianza global '*' antes de usar la configuración guiada."
                )
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as ex:
                raise SetupError(
                    "La lista actual de proxies contiene una dirección no válida."
                ) from ex
            values.append(value)
    line = ENV_KEY + "=" + ",".join(dict.fromkeys(values))
    updated = (
        re.sub(r"^" + ENV_KEY + r"=.*$", line, original, flags=re.M)
        if existing
        else original.rstrip() + "\n" + line + "\n"
    )
    recreate = ["docker", "compose", "up", "-d", "--no-build", "--force-recreate", service]
    write_private(path, updated)
    try:
        command(recreate, project)
        for _ in range(10):
            try:
                health, _ = probe(url)
                if health["scheme"] == urlsplit(url).scheme:
                    return {
                        "state": "applied",
                        "changed": True,
                        "message": "Proxy configurado y verificado. Ya puedes iniciar sesión y guardar ajustes.",
                    }
            except Exception:
                pass
            time.sleep(0.3)
        raise SetupError("No se confirmó el acceso externo después de recrear la aplicación.")
    except Exception as ex:
        if existed:
            write_private(path, original)
        else:
            path.unlink(missing_ok=True)
        try:
            command(recreate, project)
        except Exception as rollback:
            raise SetupError(
                "Se restauró el archivo anterior, pero debes revisar el arranque de Docker."
            ) from rollback
        raise SetupError("La verificación falló; se restauró la configuración anterior.") from ex


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configura el acceso del panel indicando solo su URL pública."
    )
    parser.add_argument("url", help="URL con la que abres el panel")
    parser.add_argument(
        "--apply", action="store_true", help="Aplicar el cambio detectado y verificarlo"
    )
    parser.add_argument("--project-dir", type=Path, default=PROJECT_DIR)
    parser.add_argument("--container", default="tebypass")
    parser.add_argument("--service", default="tebypass")
    args = parser.parse_args()
    try:
        url = args.url.rstrip("/")
        plan = inspect_access(url, args.project_dir, args.container)
        if args.apply:
            plan = apply_access(url, args.project_dir, args.container, args.service, plan)
        print(json.dumps(plan, ensure_ascii=False))
    except Exception as ex:
        message = (
            str(ex)
            if isinstance(ex, SetupError)
            else "No se pudo comprobar el acceso. Revisa URL, certificado y conectividad."
        )
        print(
            json.dumps({"state": "error", "changed": None, "message": message}, ensure_ascii=False)
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
