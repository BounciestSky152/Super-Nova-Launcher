"""GOG offline installers: native Linux ``.sh`` packages and Windows setup fallbacks."""
from __future__ import annotations

import io
import logging
import os
import shutil
import stat
import subprocess
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from nebula import paths
from nebula.core.download import download_file
from nebula.core.jobs import JobContext
from nebula.core.utils import which
from nebula.integrations.gog.api import GogApi

log = logging.getLogger(__name__)

LANG_PREF = ["en"]


def installers_for(product: dict, os_name: str) -> list[dict]:
    return [i for i in (product.get("downloads", {}) or {}).get("installers", []) if i.get("os") == os_name]


def pick_installer(installers: list[dict], language: str) -> dict | None:
    if not installers:
        return None
    short = (language or "en").split("-")[0].lower()
    for wanted in (short, "en"):
        for inst in installers:
            if str(inst.get("language", "")).lower() == wanted:
                return inst
    return installers[0]


def installer_languages(installers: list[dict]) -> list[tuple[str, str]]:
    out = []
    for inst in installers:
        code = inst.get("language", "")
        label = inst.get("language_full") or code
        if (code, label) not in out:
            out.append((code, label))
    return out


def download_installer(api: GogApi, session: requests.Session, ctx: JobContext, installer: dict,
                       dest_dir: Path) -> list[Path]:
    files = installer.get("files", [])
    total = int(installer.get("total_size") or sum(int(f.get("size", 0)) for f in files))
    ctx.set_total(total)
    ctx.set_done(0)
    out = []
    for idx, f in enumerate(files, 1):
        ctx.check()
        ctx.stage(f"Downloading installer ({idx}/{len(files)})")
        info = api.downlink(f["downlink"])
        real = info.get("downlink")
        if not real:
            raise IOError("GOG did not return a download link")
        chk = api.checksum_info(info.get("checksum", ""))
        name = unquote(urlparse(real).path.rsplit("/", 1)[-1]) or f"part{idx}"
        size = int(chk.get("total_size") or f.get("size") or 0)
        out.append(download_file(session, real, dest_dir / name, ctx, expected_size=size,
                                 expected_md5=chk.get("md5", "")))
    return out


class RemoteFile(io.RawIOBase):
    """An installer on GOG's CDN read with HTTP range requests, so ``zipfile`` can take single files out of it
    without downloading the rest. Reads ahead in big blocks, but never past ``limit`` (the end of the files
    wanted next), so nothing that isn't needed is downloaded."""

    BLOCK = 4 << 20

    def __init__(self, session: requests.Session, url: str, ctx: JobContext | None = None):
        super().__init__()
        self.session, self.url, self.ctx = session, url, ctx
        self.pos = 0
        self.limit = 0
        self._start, self._buf = 0, b""
        r = session.get(url, headers={"Range": "bytes=0-0"}, timeout=(15, 60))
        total = r.headers.get("Content-Range", "").rpartition("/")[2]
        if r.status_code != 206 or not total.isdigit():
            raise IOError("the download server doesn't support partial downloads")
        self.size = int(total)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def read(self, n: int = -1) -> bytes:
        n = self.size - self.pos if n is None or n < 0 else min(n, self.size - self.pos)
        out = bytearray()
        while n > 0:
            if not self._start <= self.pos < self._start + len(self._buf):
                end = max(self.pos + n, min(self.pos + self.BLOCK, self.limit or self.size))
                self._fetch(self.pos, min(end, self.size))
            piece = self._buf[self.pos - self._start:self.pos - self._start + n]
            out += piece
            self.pos += len(piece)
            n -= len(piece)
        return bytes(out)

    def _fetch(self, start: int, end: int) -> None:
        last = None
        for _attempt in range(4):
            if self.ctx:
                self.ctx.check()
            try:
                r = self.session.get(self.url, headers={"Range": f"bytes={start}-{end - 1}"}, timeout=(15, 120))
                if r.status_code != 206:
                    raise IOError(f"HTTP {r.status_code}")
                self._start, self._buf = start, r.content
                if self.ctx:
                    self.ctx.add(len(self._buf))
                if self._buf:
                    return
            except (requests.RequestException, OSError) as exc:
                last = exc
        raise IOError(f"Download failed: {last}")


GAME_DATA = "data/noarch/"


def _crc(path: Path) -> int:
    crc = 0
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            crc = zlib.crc32(block, crc)
    return crc


def _unchanged(m: zipfile.ZipInfo, dest: Path, known: list | None) -> bool:
    """Whether the installed copy of ``m`` is already this version: from the saved file list when there is one
    (cheap), otherwise by checksumming the file (installs made before the file list existed)."""
    mode = (m.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        return dest.is_symlink() and known is not None and known[0] == m.CRC
    if dest.is_symlink() or not dest.is_file() or dest.stat().st_size != m.file_size:
        return False
    if known is not None:
        return known[0] == m.CRC and known[1] == m.file_size
    return _crc(dest) == m.CRC


def install_linux_remote(session: requests.Session, ctx: JobContext, url: str, install_dir: Path,
                         old: dict | None) -> dict:
    """Install or update from a GOG ``.sh`` installer without downloading it: only the compressed data of files
    that are new or changed is fetched (``old`` is the file list saved by the previous run, path -> [crc, size]).
    Returns the new file list. Files the new version no longer has are removed."""
    install_dir.mkdir(parents=True, exist_ok=True)
    root = install_dir.resolve()
    ctx.stage("Reading the installer")
    with zipfile.ZipFile(RemoteFile(session, url, ctx)) as z:
        members = [m for m in z.infolist() if m.filename.startswith(GAME_DATA) and m.filename != GAME_DATA]
    if not members:
        raise IOError("Installer does not contain game data (data/noarch)")
    files = {m.filename[len(GAME_DATA):].rstrip("/"): [m.CRC, m.file_size] for m in members if not m.is_dir()}
    for m in members:
        dest = (install_dir / m.filename[len(GAME_DATA):]).resolve()
        if dest != root and root not in dest.parents:
            raise IOError(f"Unsafe path in installer: {m.filename}")
        if m.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
    ctx.stage("Comparing with the installed files")
    wanted = [m for m in members if not m.is_dir() and not _unchanged(
        m, install_dir / m.filename[len(GAME_DATA):], (old or {}).get(m.filename[len(GAME_DATA):]))]
    ctx.set_total(sum(m.compress_size for m in wanted))
    ctx.set_done(0)
    ctx.stage(f"Downloading {len(wanted)} file{'s' if len(wanted) != 1 else ''}" if wanted else "Up to date")
    order = sorted(members, key=lambda m: m.header_offset)
    nxt = {id(a): b.header_offset for a, b in zip(order, order[1:])}

    def run(batch: list[zipfile.ZipInfo]) -> None:
        remote = RemoteFile(session, url, ctx)
        with zipfile.ZipFile(remote) as zf:
            for i, m in enumerate(batch):
                # read ahead up to the end of the run of wanted files that starts here
                j = i
                while j + 1 < len(batch) and nxt.get(id(batch[j])) == batch[j + 1].header_offset:
                    j += 1
                remote.limit = nxt.get(id(batch[j]), remote.size)
                _write_member(zf, m, install_dir / m.filename[len(GAME_DATA):], ctx)

    if wanted:
        wanted.sort(key=lambda m: m.header_offset)
        n = max(1, min(ctx.workers, 4, len(wanted)))
        size = sum(m.compress_size for m in wanted)
        batches, cur, acc = [], [], 0
        for m in wanted:  # contiguous slices of roughly equal size, one connection each
            cur.append(m)
            acc += m.compress_size
            if acc >= size / n and len(batches) < n - 1:
                batches.append(cur)
                cur, acc = [], 0
        batches.append(cur)
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            for f in [pool.submit(run, b) for b in batches if b]:
                f.result()
    for rel in set(old or {}) - set(files):  # files the new version dropped
        p = install_dir / rel
        if p.is_symlink() or p.is_file():
            p.unlink()
    fix_permissions(install_dir)
    return files


def fix_permissions(root: Path) -> int:
    """Make programs and scripts executable where the installer's archive didn't say so (zip entries without
    Unix modes): start.sh would otherwise fail to run the game. Returns how many files were fixed."""
    fixed = 0
    for p in root.rglob("*"):
        try:
            if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o111:
                continue
            with open(p, "rb") as fh:
                head = fh.read(4)
            if head == b"\x7fELF" or head[:2] == b"#!":
                p.chmod(p.stat().st_mode | 0o111)
                fixed += 1
        except OSError:
            continue
    return fixed


def _write_member(z: zipfile.ZipFile, m: zipfile.ZipInfo, dest: Path, ctx: JobContext) -> None:
    ctx.check()
    mode = (m.external_attr >> 16) & 0xFFFF
    dest.parent.mkdir(parents=True, exist_ok=True)
    if stat.S_ISLNK(mode):
        target = z.read(m).decode("utf-8", "replace")
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        os.symlink(target, dest)
        return
    tmp = dest.with_name(f".{dest.name}.nbpart")
    with z.open(m) as src, open(tmp, "wb") as out:  # zipfile checks the CRC when the member ends
        while block := src.read(1 << 20):
            out.write(block)
    os.replace(tmp, dest)
    if mode & 0o777:
        os.chmod(dest, mode & 0o777)


def extract_linux_installer(ctx: JobContext, installer_path: Path, install_dir: Path) -> None:
    """Unpack the zip payload of a GOG MojoSetup ``.sh`` installer (``data/noarch`` → install dir)."""
    install_dir.mkdir(parents=True, exist_ok=True)
    root = install_dir.resolve()
    with zipfile.ZipFile(installer_path) as z:
        members = [m for m in z.infolist() if m.filename.startswith("data/noarch/")]
        if not members:
            raise IOError("Installer does not contain game data (data/noarch)")
        ctx.set_total(sum(m.file_size for m in members))
        ctx.set_done(0)
        ctx.stage("Extracting")
        for m in members:
            ctx.check()
            rel = m.filename[len("data/noarch/"):]
            if not rel:
                continue
            dest = (install_dir / rel)
            if root not in dest.resolve().parents and dest.resolve() != root:
                raise IOError(f"Unsafe path in installer: {m.filename}")
            mode = (m.external_attr >> 16) & 0xFFFF
            if m.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                target = z.read(m).decode("utf-8", "replace")
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                os.symlink(target, dest)
                ctx.add(m.file_size, network=False)
                continue
            with z.open(m) as src, open(dest, "wb") as dst:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    dst.write(block)
                    ctx.add(len(block), network=False)
            if mode & 0o777:
                os.chmod(dest, mode & 0o777)
    fix_permissions(install_dir)


def extract_windows_installer(ctx: JobContext, setup_exe: Path, install_dir: Path, compat=None,
                              uid: str = "") -> str:
    """Install a Windows offline installer, preferring innoextract (no Wine needed)."""
    install_dir.mkdir(parents=True, exist_ok=True)
    tool = which("innoextract")
    if tool:
        ctx.stage("Extracting with innoextract")
        tmp = install_dir.parent / f".{install_dir.name}.nbextract"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        proc = subprocess.run([tool, "--gog", "--exclude-temp", "--silent", "-d", str(tmp), str(setup_exe)],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            src = tmp / "app" if (tmp / "app").is_dir() else tmp
            for item in src.iterdir():
                dest = install_dir / item.name
                if dest.exists():
                    if dest.is_dir() and not dest.is_symlink():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            shutil.rmtree(tmp, ignore_errors=True)
            return "innoextract"
        log.warning("innoextract failed (%s), falling back to running the installer", proc.stderr.strip()[:300])
        shutil.rmtree(tmp, ignore_errors=True)
    if compat is None:
        raise IOError("innoextract is not installed and no Proton/Wine runner is available")
    ctx.stage("Running installer with Proton (silent)")
    win_dir = "Z:" + str(install_dir).replace("/", "\\")
    code = compat.run_in_prefix(uid, [str(setup_exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                                      "/NOICONS", f"/DIR={win_dir}"], cwd=str(setup_exe.parent), timeout=4 * 3600)
    if code != 0 and not any(install_dir.iterdir()):
        raise IOError(f"The installer exited with code {code}")
    return "installer"


def cache_dir_for(product_id: str) -> Path:
    p = paths.installers_cache() / str(product_id)
    p.mkdir(parents=True, exist_ok=True)
    return p
