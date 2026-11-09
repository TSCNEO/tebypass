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
        body=self.client_address[0].encode()+b'\n'+b'x'*(256*1024)
        self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        body=self.rfile.read(int(self.headers['Content-Length']))
        assert body==b'x'*(256*1024)
        self.send_response(200); self.end_headers()
        self.wfile.write(str(len(body)).encode())
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
    source,body=response.read().split(b'\n',1)
    assert source.decode()=='192.0.2.26', 'Forwarded connection must be NATed by the gateway'
    assert body==b'x'*(256*1024)
request=urllib.request.Request('http://203.0.113.7:8080',data=b'x'*(256*1024),method='POST')
with urllib.request.urlopen(request,timeout=3) as response:
    assert response.read()==b'262144'
print('Forwarded DNS/HTTP passed; NAT confirmed; 256 KiB upload/download passed')
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


def configure_tunnel(gateway: str, endpoint: str) -> None:
    """Create both peers using keys generated inside disposable containers."""
    for container in (gateway, endpoint):
        docker(
            "exec",
            container,
            "sh",
            "-c",
            "set -e; umask 077; wg genkey > /tmp/test-wg.key; wg pubkey < /tmp/test-wg.key > /tmp/test-wg.pub",
        )
    gateway_public = docker("exec", gateway, "cat", "/tmp/test-wg.pub").stdout.strip()
    endpoint_public = docker("exec", endpoint, "cat", "/tmp/test-wg.pub").stdout.strip()
    docker("exec", endpoint, "ip", "link", "add", "wg-peer", "type", "wireguard")
    docker("exec", endpoint, "ip", "addr", "add", "198.51.100.1/30", "dev", "wg-peer")
    docker(
        "exec",
        endpoint,
        "wg",
        "set",
        "wg-peer",
        "private-key",
        "/tmp/test-wg.key",
        "listen-port",
        "51820",
        "peer",
        gateway_public,
        "allowed-ips",
        "198.51.100.2/32",
    )
    docker("exec", endpoint, "ip", "link", "set", "wg-peer", "up")
    write_config = r"""
import os,pathlib,sys
p=pathlib.Path('/data/wireguard/wg0.conf')
p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
key=pathlib.Path('/tmp/test-wg.key').read_text().strip()
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as handle:
    handle.write(f"[Interface]\nPrivateKey = {key}\nAddress = 198.51.100.2/30\n[Peer]\nPublicKey = {sys.argv[1]}\nAllowedIPs = 203.0.113.7/32\nEndpoint = 192.0.2.7:51820\n")
"""
    docker("exec", gateway, "python3", "-c", write_config, endpoint_public)
    control(gateway, {"cmd": "reload"})


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
        configure_tunnel(gateway, endpoint)
        control(gateway, {"cmd": "sync", "add": ["203.0.113.7/32"], "remove": []})
        through_tunnel = CLIENT.replace("192.0.2.26", "198.51.100.2")
        print(
            "Tunnel probe:", docker("exec", client, "python3", "-c", through_tunnel).stdout.strip()
        )
        handshake = docker("exec", gateway, "wg", "show", "wg0", "latest-handshakes").stdout
        assert any(int(line.split()[-1]) > 0 for line in handshake.splitlines())
        docker("exec", gateway, "ip", "link", "del", "wg0")
        blocked = docker("exec", client, "python3", "-c", CLIENT, check=False)
        assert blocked.returncode != 0, "Tunnel loss leaked selected traffic onto the normal route"
        control(gateway, {"cmd": "flush"})
        print("Recovery probe:", docker("exec", client, "python3", "-c", CLIENT).stdout.strip())
        print(
            "Client forwarding test passed: direct DNS/HTTP, tunnel DNS/HTTP, tunnel loss and recovery"
        )
    finally:
        for container in containers:
            docker("rm", "-f", container, check=False)
        docker("network", "rm", name, check=False)


if __name__ == "__main__":
    main()
