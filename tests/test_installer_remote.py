"""Linux installers are read straight from the server: only new or changed files are downloaded."""
import io
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from nebula.integrations.gog import offline


def installer(files: dict) -> bytes:
    """A MojoSetup-style .sh: a shell header with the game data zip appended."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scripts/config.lua", "-- setup")
        for name, data in files.items():
            info = zipfile.ZipInfo("data/noarch/" + name)
            info.external_attr = 0o100755 << 16 if name.endswith(".sh") else 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return b"#!/bin/sh\n# makeself header\n" + os.urandom(2048) + buf.getvalue()


class Ctx:
    workers, cancelled = 3, False

    def __init__(self):
        self.downloaded = 0

    def check(self):
        pass

    def stage(self, _text):
        pass

    def set_total(self, _n):
        pass

    def set_done(self, _n):
        pass

    def add(self, n, network=True):
        if network:
            self.downloaded += n


def serve(blob_ref: dict):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            data = blob_ref["data"]
            start, _, end = self.headers.get("Range", "bytes=0-").removeprefix("bytes=").partition("-")
            start, end = int(start), int(end) if end else len(data) - 1
            body = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{start + len(body) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/game.sh"


def test_install_then_delta_update(tmp_path):
    big = os.urandom(3 << 20)  # incompressible, so its download is easy to tell apart
    v1 = {"game.bin": big, "start.sh": b"#!/bin/sh\necho v1\n", "old.txt": b"gone in v2", "lib/a.so": b"A" * 5000}
    blob = {"data": installer(v1)}
    srv, url = serve(blob)
    try:
        s, ctx = requests.Session(), Ctx()
        dest = tmp_path / "Game"
        files = offline.install_linux_remote(s, ctx, url, dest, None)
        assert (dest / "game.bin").read_bytes() == big and (dest / "lib/a.so").read_bytes() == b"A" * 5000
        assert os.access(dest / "start.sh", os.X_OK) and set(files) == set(v1)
        assert ctx.downloaded < len(blob["data"])  # the shell header and setup scripts were never fetched

        v2 = dict(v1, **{"start.sh": b"#!/bin/sh\necho v2\n", "new.dat": b"added"})
        del v2["old.txt"]
        blob["data"] = installer(v2)
        ctx2 = Ctx()
        files2 = offline.install_linux_remote(s, ctx2, url, dest, files)
        assert (dest / "start.sh").read_bytes().endswith(b"v2\n") and (dest / "new.dat").read_bytes() == b"added"
        assert not (dest / "old.txt").exists() and (dest / "game.bin").read_bytes() == big
        assert ctx2.downloaded < len(big) // 2  # the big unchanged file was not downloaded again
        assert set(files2) == set(v2)

        # no saved file list (an install from before): unchanged files are recognised by checksum
        ctx3 = Ctx()
        offline.install_linux_remote(s, ctx3, url, dest, None)
        assert ctx3.downloaded < len(big) // 2
    finally:
        srv.shutdown()


def test_linux_game_instances(services, monkeypatch, tmp_path):
    """A second instance installs next to the first, at the server's version, and both show in the library."""
    from pathlib import Path

    from conftest import make_ctx
    from nebula.core.models import Game

    gog = services.integrations["gog"]
    monkeypatch.setattr(gog, "connected", lambda: True)
    monkeypatch.setattr(gog, "owned_ids", lambda refresh=False: {"123"})
    blob = {"data": installer({"game.bin": b"v1" * 1000, "start.sh": b"#!/bin/sh\n"})}
    srv, url = serve(blob)
    product = {"id": 123, "expanded_dlcs": [], "downloads": {"installers": [
        {"id": "installer_linux_en", "os": "linux", "language": "en", "version": "1.0",
         "files": [{"downlink": "dl", "size": 1}], "total_size": 1}]}}
    monkeypatch.setattr(gog.api, "product", lambda pid, expand=(): product)
    monkeypatch.setattr(gog.api, "downlink", lambda link: {"downlink": url})
    try:
        g1 = gog._offline(Game("gog", "123", "Test Game"), make_ctx(), "linux", base_dir=tmp_path)
        assert g1.installed and (tmp_path / "Test Game" / "game.bin").read_bytes() == b"v1" * 1000

        product["downloads"]["installers"][0]["version"] = "2.0"
        blob["data"] = installer({"game.bin": b"v2" * 1000, "start.sh": b"#!/bin/sh\n"})
        g2 = gog.install_instance(gog.new_instance(g1), make_ctx(), gog.rkey(g1), "", "2.0")
        assert g2.uid == "gog:123~2" and g2.installed and g2.version == "2.0"
        assert (Path(g2.install_path) / "game.bin").read_bytes() == b"v2" * 1000
        assert (Path(g1.install_path) / "game.bin").read_bytes() == b"v1" * 1000  # the first copy is untouched
        assert gog.install_record("123")["version"] == "1.0" and gog.install_record("123~2")["instance_name"] == "2.0"

        monkeypatch.setattr(gog.api, "library", lambda: [{"id": 123, "title": "Test Game"}])
        games = {g.uid: g for g in gog.fetch_library()}
        assert set(games) == {"gog:123", "gog:123~2"} and games["gog:123~2"].key == games["gog:123"].key

        gog.uninstall(g2, make_ctx())
        assert gog.install_record("123~2") is None and gog.install_record("123")["version"] == "1.0"
    finally:
        srv.shutdown()
