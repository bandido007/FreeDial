"""A scripted SIP phone for the lab: registers, rings, answers, rejects, ignores, hangs up.

Used by lab/calltests.py to play the customer and the agents against the real
Asterisk, so the brain's call tests need nobody at a Zoiper. No audio: the SDP
offers PCMU on a port that swallows whatever Asterisk sends.

    phone = Phone("t-agent1", "secret")
    await phone.start(); await phone.register()
    phone.mode = "answer"            # or "reject" (486) or "ignore" (rings until CANCEL)
    call = await phone.ringing()     # the next incoming INVITE
    out = await phone.dial("800")    # an outgoing call; out.answered / out.ended are events
    await out.hangup()

Run on its own as a fake agent:  python lab/sipphone.py t-agent1 'secret' --mode answer
"""
import argparse
import asyncio
import hashlib
import re
import secrets
import time

COMPACT = {"v": "via", "f": "from", "t": "to", "i": "call-id", "m": "contact", "l": "content-length", "c": "content-type"}


def md5(text):
    return hashlib.md5(text.encode()).hexdigest()  # noqa: S324 (SIP digest is MD5)


def log(phone, msg):
    print(f"{time.strftime('%H:%M:%S')}  [{phone}] {msg}", flush=True)


class Message:
    def __init__(self, raw):
        head, _, self.body = raw.partition("\r\n\r\n")
        lines = head.split("\r\n")
        self.first = lines[0]
        self.headers = []
        for line in lines[1:]:
            name, _, value = line.partition(":")
            name = name.strip().lower()
            self.headers.append((COMPACT.get(name, name), value.strip()))
        parts = self.first.split(" ", 2)
        self.is_response = parts[0] == "SIP/2.0"
        self.status = int(parts[1]) if self.is_response else None
        self.reason = parts[2] if self.is_response else None
        self.method = None if self.is_response else parts[0]
        self.uri = None if self.is_response else parts[1]

    def get(self, name, default=""):
        for key, value in self.headers:
            if key == name:
                return value
        return default

    def all(self, name):
        return [value for key, value in self.headers if key == name]

    @property
    def cseq(self):
        number, method = self.get("cseq").split()
        return int(number), method


def tag_of(header):
    match = re.search(r";\s*tag=([^;>\s]+)", header)
    return match.group(1) if match else None


def uri_of(header):
    match = re.search(r"<([^>]+)>", header)
    return match.group(1) if match else header.split(";")[0].strip()


class Call:
    """One dialog, incoming or outgoing."""

    def __init__(self, phone, call_id, incoming):
        self.phone = phone
        self.call_id = call_id
        self.incoming = incoming
        self.local_tag = secrets.token_hex(4)
        self.remote_tag = None
        self.local_uri = self.remote_uri = self.remote_target = None
        self.cseq = 0
        self.invite = None  # the INVITE that opened it (incoming) or our last INVITE (outgoing)
        self.answered = asyncio.Event()
        self.ended = asyncio.Event()
        self.end_reason = None
        self.final_status = None
        self.caller_id = None
        self.created = time.monotonic()

    def __repr__(self):
        return f"<Call {'in' if self.incoming else 'out'} {self.call_id[:8]} {self.end_reason or ''}>"

    def finish(self, reason):
        if not self.ended.is_set():
            self.end_reason = reason
            self.ended.set()
            log(self.phone.user, f"call {self.call_id[:8]} ended: {reason}")

    async def answer(self):
        await self.phone.answer(self)

    async def reject(self, status=486, reason="Busy Here"):
        self.phone.respond(self.invite, status, reason, to_tag=self.local_tag)
        self.finish(f"rejected {status}")

    async def hangup(self):
        await self.phone.bye(self)


class Phone:
    def __init__(self, user, password, server="127.0.0.1:5062", mode="answer", answer_delay=0.3):
        self.user, self.password = user, password
        host, port = server.rsplit(":", 1)
        self.server = (host, int(port))
        self.mode = mode
        self.answer_delay = answer_delay
        self.calls = {}
        self.waiters = {}  # (call-id, cseq, method) -> future for the final response
        self.provisional = {}  # same key -> callback for 1xx
        self.incoming = asyncio.Queue()
        self.transport = None
        self.rtp = None
        self.reg_call_id = secrets.token_hex(8) + "@lab"
        self.reg_tag = secrets.token_hex(4)
        self.reg_cseq = 0
        self.registered = False

    # ── transport ──────────────────────────────────────────────────────────
    async def start(self):
        loop = asyncio.get_running_loop()
        phone = self

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                phone.received(data.decode(errors="replace"))

        class Sink(asyncio.DatagramProtocol):
            pass

        self.transport, _ = await loop.create_datagram_endpoint(Proto, local_addr=("127.0.0.1", 0))
        self.rtp, _ = await loop.create_datagram_endpoint(Sink, local_addr=("127.0.0.1", 0))
        self.ip, self.port = self.transport.get_extra_info("sockname")[:2]
        self.rtp_port = self.rtp.get_extra_info("sockname")[1]
        return self

    async def close(self):
        for call in list(self.calls.values()):
            if not call.ended.is_set() and call.answered.is_set():
                try:
                    await asyncio.wait_for(self.bye(call), 2)
                except Exception:
                    pass
        if self.registered:
            try:
                await asyncio.wait_for(self.register(expires=0), 3)
            except Exception:
                pass
        self.transport.close()
        self.rtp.close()

    def send(self, text):
        self.transport.sendto(text.encode(), self.server)

    def received(self, text):
        if not text.strip():
            return
        try:
            msg = Message(text)
        except Exception as exc:
            log(self.user, f"unparseable message: {exc}")
            return
        if msg.is_response:
            self.on_response(msg)
        else:
            self.on_request(msg)

    def via(self):
        return f"SIP/2.0/UDP {self.ip}:{self.port};branch=z9hG4bK{secrets.token_hex(8)};rport"

    def contact(self):
        return f"<sip:{self.user}@{self.ip}:{self.port}>"

    def sdp(self):
        sess = int(time.time())
        return (
            "v=0\r\n"
            f"o=- {sess} {sess} IN IP4 {self.ip}\r\n"
            "s=lab\r\n"
            f"c=IN IP4 {self.ip}\r\n"
            "t=0 0\r\n"
            f"m=audio {self.rtp_port} RTP/AVP 0 101\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=rtpmap:101 telephone-event/8000\r\n"
            "a=fmtp:101 0-16\r\n"
            "a=sendrecv\r\n"
        )

    def build(self, first, headers, body=""):
        lines = [first] + [f"{name}: {value}" for name, value in headers]
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body.encode())}")
        return "\r\n".join(lines) + "\r\n\r\n" + body

    # ── requests we send ───────────────────────────────────────────────────
    async def transact(self, text, key, timeout=10, on_provisional=None):
        future = asyncio.get_running_loop().create_future()
        self.waiters[key] = future
        if on_provisional:
            self.provisional[key] = on_provisional
        self.send(text)
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.waiters.pop(key, None)
            self.provisional.pop(key, None)

    def authorization(self, challenge_msg, method, uri):
        for name in ("www-authenticate", "proxy-authenticate"):
            for value in challenge_msg.all(name):
                if not value.lower().startswith("digest"):
                    continue
                params = dict(re.findall(r'(\w+)="?([^",]+)"?', value[6:]))
                if params.get("algorithm", "MD5").upper() != "MD5":
                    continue
                realm, nonce = params["realm"], params["nonce"]
                ha1 = md5(f"{self.user}:{realm}:{self.password}")
                ha2 = md5(f"{method}:{uri}")
                fields = [f'username="{self.user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"', "algorithm=MD5"]
                if "auth" in params.get("qop", ""):
                    cnonce, nc = secrets.token_hex(8), "00000001"
                    fields += [f'response="{md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")}"', f'cnonce="{cnonce}"', f"nc={nc}", "qop=auth"]
                else:
                    fields.append(f'response="{md5(f"{ha1}:{nonce}:{ha2}")}"')
                if "opaque" in params:
                    fields.append(f'opaque="{params["opaque"]}"')
                header = "Authorization" if name == "www-authenticate" else "Proxy-Authorization"
                return header, "Digest " + ", ".join(fields)
        return None

    async def register(self, expires=300):
        uri = f"sip:{self.server[0]}:{self.server[1]}"
        auth = None
        for _ in range(2):
            self.reg_cseq += 1
            headers = [
                ("Via", self.via()),
                ("Max-Forwards", "70"),
                ("From", f"<sip:{self.user}@{self.server[0]}>;tag={self.reg_tag}"),
                ("To", f"<sip:{self.user}@{self.server[0]}>"),
                ("Call-ID", self.reg_call_id),
                ("CSeq", f"{self.reg_cseq} REGISTER"),
                ("Contact", self.contact()),
                ("Expires", str(expires)),
                ("User-Agent", "algodial-lab-phone"),
            ]
            if auth:
                headers.append(auth)
            response = await self.transact(self.build(f"REGISTER {uri} SIP/2.0", headers), (self.reg_call_id, self.reg_cseq, "REGISTER"))
            if response.status in (401, 407) and auth is None:
                auth = self.authorization(response, "REGISTER", uri)
                if auth:
                    continue
            break
        self.registered = response.status == 200 and expires > 0
        log(self.user, f"{'REGISTER' if expires else 'unREGISTER'} -> {response.status} {response.reason}")
        return response.status

    async def dial(self, number, timeout=30):
        call = Call(self, secrets.token_hex(10) + "@lab", incoming=False)
        call.local_uri = f"sip:{self.user}@{self.server[0]}"
        call.remote_uri = f"sip:{number}@{self.server[0]}:{self.server[1]}"
        call.remote_target = call.remote_uri
        self.calls[call.call_id] = call
        asyncio.create_task(self._invite(call, timeout))
        return call

    async def _invite(self, call, timeout):
        auth = None
        for _ in range(2):
            call.cseq += 1
            branch_via = self.via()
            headers = [
                ("Via", branch_via),
                ("Max-Forwards", "70"),
                ("From", f"<{call.local_uri}>;tag={call.local_tag}"),
                ("To", f"<{call.remote_uri}>"),
                ("Call-ID", call.call_id),
                ("CSeq", f"{call.cseq} INVITE"),
                ("Contact", self.contact()),
                ("User-Agent", "algodial-lab-phone"),
            ]
            if auth:
                headers.append(auth)
            call.invite_headers = headers
            key = (call.call_id, call.cseq, "INVITE")
            try:
                response = await self.transact(self.build(f"INVITE {call.remote_uri} SIP/2.0", headers, self.sdp()), key, timeout=timeout)
            except asyncio.TimeoutError:
                call.finish("no final answer")
                return
            if response.status >= 300:
                self.ack_failure(call, response, branch_via)
                if response.status in (401, 407) and auth is None:
                    auth = self.authorization(response, "INVITE", call.remote_uri)
                    if auth:
                        continue
                call.final_status = response.status
                call.finish(f"refused {response.status} {response.reason}")
                return
            break
        call.final_status = response.status
        call.remote_tag = tag_of(response.get("to"))
        call.remote_target = uri_of(response.get("contact")) or call.remote_uri
        self.ack_success(call)
        call.answered.set()
        log(self.user, f"call {call.call_id[:8]} answered: {response.status} {response.reason}")

    def ack_failure(self, call, response, via):
        headers = [
            ("Via", via),
            ("Max-Forwards", "70"),
            ("From", f"<{call.local_uri}>;tag={call.local_tag}"),
            ("To", response.get("to")),
            ("Call-ID", call.call_id),
            ("CSeq", f"{call.cseq} ACK"),
        ]
        self.send(self.build(f"ACK {call.remote_uri} SIP/2.0", headers))

    def ack_success(self, call):
        headers = [
            ("Via", self.via()),
            ("Max-Forwards", "70"),
            ("From", f"<{call.local_uri}>;tag={call.local_tag}"),
            ("To", f"<{call.remote_uri}>;tag={call.remote_tag}"),
            ("Call-ID", call.call_id),
            ("CSeq", f"{call.cseq} ACK"),
        ]
        self.send(self.build(f"ACK {call.remote_target} SIP/2.0", headers))

    async def bye(self, call):
        if call.ended.is_set():
            return
        if not call.answered.is_set():
            if call.incoming:
                await call.reject(603, "Decline")
            else:
                await self.cancel(call)
            return
        auth = None
        for _ in range(2):
            call.cseq += 1
            headers = [
                ("Via", self.via()),
                ("Max-Forwards", "70"),
                ("From", f"<{call.local_uri}>;tag={call.local_tag}"),
                ("To", f"<{call.remote_uri}>;tag={call.remote_tag}"),
                ("Call-ID", call.call_id),
                ("CSeq", f"{call.cseq} BYE"),
            ]
            if auth:
                headers.append(auth)
            response = await self.transact(self.build(f"BYE {call.remote_target} SIP/2.0", headers), (call.call_id, call.cseq, "BYE"))
            if response.status in (401, 407) and auth is None:
                auth = self.authorization(response, "BYE", call.remote_target)
                if auth:
                    continue
            break
        call.finish(f"we hung up ({response.status})")

    async def cancel(self, call):
        via = next(value for name, value in call.invite_headers if name == "Via")
        headers = [
            ("Via", via),
            ("Max-Forwards", "70"),
            ("From", f"<{call.local_uri}>;tag={call.local_tag}"),
            ("To", f"<{call.remote_uri}>"),
            ("Call-ID", call.call_id),
            ("CSeq", f"{call.cseq} CANCEL"),
        ]
        await self.transact(self.build(f"CANCEL {call.remote_uri} SIP/2.0", headers), (call.call_id, call.cseq, "CANCEL"))
        call.finish("we cancelled")

    # ── what Asterisk sends us ─────────────────────────────────────────────
    def on_response(self, msg):
        number, method = msg.cseq
        key = (msg.get("call-id"), number, method)
        if msg.status < 200:
            callback = self.provisional.get(key)
            if callback:
                callback(msg)
            return
        future = self.waiters.get(key)
        if future and not future.done():
            future.set_result(msg)
            return
        call = self.calls.get(msg.get("call-id"))
        if call and method == "INVITE" and msg.status < 300 and call.answered.is_set():
            self.ack_success(call)  # a retransmitted 200 OK: ACK again

    def respond(self, request, status, reason, to_tag=None, body="", contact=False):
        to = request.get("to")
        if to_tag and not tag_of(to):
            to = f"{to};tag={to_tag}"
        headers = [("Via", via) for via in request.all("via")]
        headers += [("From", request.get("from")), ("To", to), ("Call-ID", request.get("call-id")), ("CSeq", request.get("cseq"))]
        if contact:
            headers.append(("Contact", self.contact()))
        headers.append(("User-Agent", "algodial-lab-phone"))
        self.send(self.build(f"SIP/2.0 {status} {reason}", headers, body))

    def on_request(self, msg):
        call_id = msg.get("call-id")
        call = self.calls.get(call_id)
        if msg.method == "INVITE" and call is None:
            self.on_invite(msg)
        elif msg.method == "INVITE":  # re-INVITE inside a dialog
            self.respond(msg, 200, "OK", to_tag=call.local_tag if call.incoming else None, body=self.sdp(), contact=True)
        elif msg.method == "ACK":
            pass
        elif msg.method == "CANCEL":
            self.respond(msg, 200, "OK")
            if call and not call.answered.is_set():
                self.respond(call.invite, 487, "Request Terminated", to_tag=call.local_tag)
                call.finish("cancelled by Asterisk")
        elif msg.method == "BYE":
            self.respond(msg, 200, "OK")
            if call:
                call.finish("Asterisk hung up (BYE)")
        else:  # OPTIONS, NOTIFY, UPDATE, INFO: just say yes
            self.respond(msg, 200, "OK", body=self.sdp() if msg.method == "UPDATE" and msg.body else "")

    def on_invite(self, msg):
        call = Call(self, msg.get("call-id"), incoming=True)
        call.invite = msg
        call.remote_tag = tag_of(msg.get("from"))
        call.remote_uri = uri_of(msg.get("from"))
        call.local_uri = uri_of(msg.get("to"))
        call.remote_target = uri_of(msg.get("contact")) or call.remote_uri
        call.caller_id = msg.get("from")
        self.calls[call.call_id] = call
        self.respond(msg, 100, "Trying")
        self.respond(msg, 180, "Ringing", to_tag=call.local_tag)
        log(self.user, f"ringing: {call.caller_id} (mode {self.mode})")
        self.incoming.put_nowait(call)
        if self.mode == "answer":
            asyncio.get_running_loop().call_later(self.answer_delay, lambda: asyncio.ensure_future(self.answer(call)))
        elif self.mode == "reject":
            asyncio.get_running_loop().call_later(self.answer_delay, lambda: asyncio.ensure_future(call.reject()))

    async def answer(self, call):
        if call.ended.is_set() or call.answered.is_set():
            return
        self.respond(call.invite, 200, "OK", to_tag=call.local_tag, body=self.sdp(), contact=True)
        call.answered.set()
        log(self.user, f"answered {call.call_id[:8]}")

    async def ringing(self, timeout=15):
        return await asyncio.wait_for(self.incoming.get(), timeout)


async def main():
    parser = argparse.ArgumentParser(description="A scripted lab SIP phone (no audio).")
    parser.add_argument("user")
    parser.add_argument("password")
    parser.add_argument("--server", default="127.0.0.1:5062")
    parser.add_argument("--mode", choices=["answer", "reject", "ignore"], default="answer")
    parser.add_argument("--dial", help="call this number, e.g. 800, and hang up after --talk seconds")
    parser.add_argument("--talk", type=float, default=10)
    args = parser.parse_args()

    phone = await Phone(args.user, args.password, args.server, mode=args.mode).start()
    if await phone.register() != 200:
        return 1
    try:
        if args.dial:
            call = await phone.dial(args.dial)
            done = asyncio.ensure_future(call.ended.wait())
            await asyncio.wait([asyncio.ensure_future(call.answered.wait()), done], return_when=asyncio.FIRST_COMPLETED)
            if call.answered.is_set():
                try:
                    await asyncio.wait_for(call.ended.wait(), args.talk)
                except asyncio.TimeoutError:
                    await call.hangup()
        else:
            log(phone.user, "waiting for calls, Ctrl-C to stop")
            await asyncio.Event().wait()
    finally:
        await phone.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
