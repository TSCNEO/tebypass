"""Exercise a client through a one-armed gateway in an isolated Docker test LAN."""

import argparse
import json
import subprocess
import time
import uuid

ENDPOINT = r"""
import http.server, socket, threading, time
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(self.client_address[0].encode())
    def log_message(self, *args): pass

def dns():
    server=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    for attempt in range(30):
        try: server.bind(('203.0.113.7',53)); break
        except OSError: time.sleep(.1)
    else: raise RuntimeError('DNS fixture address not ready')
    while True:
        query, address=server.recvfrom(4096)
        answer=query[:2]+b'\x81\x80'+query[4:6]+b'\x00\x01\x00\x00\x00\x00'+query[12:]
        answer+=b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04'+socket.inet_aton('203.0.113.7')
        server.sendto(answer,address)
threading.Thread(target=dns,daemon=True).start()
http.server.HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
"""
CLIENT = r"""
import socket,urllib.request
query=b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03org\x00\x00\x01\x00\x01'
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
    udp.settimeout(3); udp.sendto(query,('203.0.113.7',53)); answer=udp.recv(4096)
    assert socket.inet_ntoa(answer[-4:])=='203.0.113.7'
with urllib.request.urlopen('http://203.0.113.7:8080',timeout=3) as response:
    assert response.status==200
    assert response.read().decode()=='192.0.2.26', 'Forwarded connection must be NATed by the gateway'
print('Forwarded DNS and HTTP passed; reply path uses gateway NAT')
"""


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=40
    )
    if check and result.returncode:
        error = subprocess.CalledProcessError(result.returncode, result.args)
        error.add_note(result.stderr)
        raise error
    return result


def control(container: str, message: dict[str, object]) -> None:
    code = (
        "import socket,json; c=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
        "c.connect('/data/gateway.sock'); c.sendall("
        + repr(json.dumps(message).encode() + b"\n")
        + "); "
        "r=json.loads(c.makefile().readline()); assert r['ok'], r"
    )
    docker("exec", container, "python3", "-c", code)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="tebypass-gateway:verify")
    args = parser.parse_args()
    name = "tbp-forward-" + uuid.uuid4().hex[:8]
    gateway, client, endpoint = (name + suffix for suffix in ("-gw", "-client", "-endpoint"))
    containers = [gateway, client, endpoint]
    try:
        docker("network", "create", "--subnet", "192.0.2.0/24", name)
        docker(
            "run",
            "-d",
            "--name",
            endpoint,
            "--network",
            name,
            "--ip",
            "192.0.2.7",
            "--cap-add",
            "NET_ADMIN",
            "--entrypoint",
            "python3",
            args.image,
            "-u",
            "-c",
            ENDPOINT,
        )
        docker("exec", endpoint, "ip", "addr", "add", "203.0.113.7/32", "dev", "eth0")
        docker(
            "run",
            "-d",
            "--name",
            gateway,
            "--network",
            name,
            "--ip",
            "192.0.2.26",
            "--cap-add",
            "NET_ADMIN",
            "--sysctl",
            "net.ipv4.ip_forward=1",
            args.image,
        )
        for _ in range(30):
            try:
                control(gateway, {"cmd": "ping"})
                break
            except subprocess.CalledProcessError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Gateway control server did not become ready")
        docker(
            "exec",
            gateway,
            "ip",
            "route",
            "replace",
            "203.0.113.7/32",
            "via",
            "192.0.2.7",
            "dev",
            "eth0",
        )
        docker(
            "run",
            "-d",
            "--name",
            client,
            "--network",
            name,
            "--ip",
            "192.0.2.10",
            "--cap-add",
            "NET_ADMIN",
            "--entrypoint",
            "sleep",
            args.image,
            "300",
        )
        docker("exec", client, "ip", "route", "replace", "default", "via", "192.0.2.26")
        print(
            "Gateway-local probe:", docker("exec", gateway, "python3", "-c", CLIENT).stdout.strip()
        )
        for chain in ("prerouting", "forward", "postrouting"):
            docker("exec", gateway, "nft", "insert", "rule", "inet", "tebypass", chain, "counter")
        probe = docker("exec", client, "python3", "-c", CLIENT, check=False)
        if probe.returncode:
            print(probe.stderr)
            print(docker("exec", gateway, "nft", "list", "table", "inet", "tebypass").stdout)
            print(docker("exec", client, "ip", "route").stdout)
            raise RuntimeError("Forwarded client probe failed")
        print(probe.stdout.strip())
        control(gateway, {"cmd": "sync", "add": ["203.0.113.7/32"], "remove": []})
        blocked = docker("exec", client, "python3", "-c", CLIENT, check=False)
        assert blocked.returncode != 0, "Selected traffic escaped without a tunnel"
        control(gateway, {"cmd": "flush"})
        print(docker("exec", client, "python3", "-c", CLIENT).stdout.strip())
        print("Client forwarding test passed: normal traffic, selected fail-closed, recovery")
    finally:
        for container in containers:
            docker("rm", "-f", container, check=False)
        docker("network", "rm", name, check=False)


if __name__ == "__main__":
    main()
