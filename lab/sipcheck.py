"""Register a SIP account with Asterisk the way a phone does, then unregister.

    python lab/sipcheck.py 1003 'the-sip-password'
    python lab/sipcheck.py 1003 'the-sip-password' --server 127.0.0.1:5062

Prints what Asterisk answered. Exit 0 = registered, 1 = refused, 2 = no answer.
Proves an account works without opening Zoiper (M1 "Done when").
"""

import argparse
import hashlib
import re
import secrets
import socket
import sys


def md5(text):
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324 (SIP digest is MD5)


def parse_challenge(response):
    """The MD5 WWW-Authenticate challenge, as a dict (Asterisk may also offer SHA-256)."""
    for line in response.split("\r\n"):
        if line.lower().startswith("www-authenticate:") and "digest" in line.lower():
            params = dict(re.findall(r'(\w+)="?([^",]+)"?', line.split(":", 1)[1]))
            if params.get("algorithm", "MD5").upper() == "MD5":
                return params
    return None


def status_of(response):
    first = response.split("\r\n", 1)[0]
    parts = first.split(" ", 2)
    return int(parts[1]), first


class Registrar:
    def __init__(self, user, password, host, port):
        self.user, self.password, self.host, self.port = user, password, host, port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(3)
        self.sock.connect((host, port))
        self.local_ip, self.local_port = self.sock.getsockname()
        self.call_id = secrets.token_hex(8) + "@algodial-sipcheck"
        self.tag = secrets.token_hex(4)
        self.cseq = 0

    def request(self, expires, authorization=None):
        self.cseq += 1
        uri = f"sip:{self.host}:{self.port}"
        lines = [
            f"REGISTER {uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch=z9hG4bK{secrets.token_hex(6)};rport",
            "Max-Forwards: 70",
            f"From: <sip:{self.user}@{self.host}>;tag={self.tag}",
            f"To: <sip:{self.user}@{self.host}>",
            f"Call-ID: {self.call_id}",
            f"CSeq: {self.cseq} REGISTER",
            f"Contact: <sip:{self.user}@{self.local_ip}:{self.local_port}>",
            f"Expires: {expires}",
            "User-Agent: algodial-sipcheck",
        ]
        if authorization:
            lines.append(f"Authorization: {authorization}")
        lines += ["Content-Length: 0", "", ""]
        self.sock.send("\r\n".join(lines).encode())
        while True:  # skip provisional answers and any OPTIONS ping from Asterisk
            data = self.sock.recv(65535).decode(errors="replace")
            if data.startswith("SIP/2.0") and f"CSeq: {self.cseq} REGISTER" in data:
                return data

    def authorization(self, challenge):
        uri = f"sip:{self.host}:{self.port}"
        realm, nonce = challenge["realm"], challenge["nonce"]
        ha1 = md5(f"{self.user}:{realm}:{self.password}")
        ha2 = md5(f"REGISTER:{uri}")
        fields = [
            f'username="{self.user}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            "algorithm=MD5",
        ]
        if "auth" in challenge.get("qop", ""):
            cnonce, nc = secrets.token_hex(8), "00000001"
            fields += [
                f'response="{md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")}"',
                f'cnonce="{cnonce}"',
                f"nc={nc}",
                "qop=auth",
            ]
        else:
            fields.append(f'response="{md5(f"{ha1}:{nonce}:{ha2}")}"')
        if "opaque" in challenge:
            fields.append(f'opaque="{challenge["opaque"]}"')
        return "Digest " + ", ".join(fields)

    def register(self, expires):
        response = self.request(expires)
        code, line = status_of(response)
        if code == 401:
            challenge = parse_challenge(response)
            if challenge is None:
                return code, line + " (no MD5 challenge offered)"
            code, line = status_of(self.request(expires, self.authorization(challenge)))
        return code, line


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("user")
    parser.add_argument("password")
    parser.add_argument("--server", default="127.0.0.1:5062")
    args = parser.parse_args()
    host, port = args.server.rsplit(":", 1)

    phone = Registrar(args.user, args.password, host, int(port))
    try:
        code, line = phone.register(expires=60)
    except TimeoutError:
        print(f"no answer from {args.server}")
        return 2
    print(f"REGISTER {args.user} -> {line}")
    if code != 200:
        return 1
    _, line = phone.register(expires=0)
    print(f"unREGISTER {args.user} -> {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
