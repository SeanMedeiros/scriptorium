"""Object storage abstraction.

Keys are content-addressed: ``<namespace>/<sha256[:2]>/<sha256>``. Objects are
written once; ``put`` on an existing key with identical content is a no-op, and
with different content raises ``ImmutableObjectError`` — original research
material can never be silently overwritten.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import get_settings

CHUNK = 1024 * 1024


class ImmutableObjectError(Exception):
    pass


class ObjectNotFound(Exception):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int


class StorageBackend(Protocol):
    name: str

    def put_stream(self, namespace: str, stream: BinaryIO) -> StoredObject: ...
    def open(self, key: str) -> BinaryIO: ...
    def iter_bytes(self, key: str) -> Iterator[bytes]: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def delete(self, key: str) -> None: ...
    def healthcheck(self) -> None: ...
    def iter_objects(self) -> Iterator[tuple[str, int, float]]: ...  # (key, size, modified epoch seconds)


def _spool_and_hash(stream: BinaryIO) -> tuple[tempfile.SpooledTemporaryFile, str, int]:
    h = hashlib.sha256()
    size = 0
    spool = tempfile.SpooledTemporaryFile(max_size=32 * CHUNK)
    while chunk := stream.read(CHUNK):
        h.update(chunk)
        size += len(chunk)
        spool.write(chunk)
    spool.seek(0)
    return spool, h.hexdigest(), size


def _key(namespace: str, digest: str) -> str:
    import re

    if not re.fullmatch(r"[a-z0-9_-]+(/[a-z0-9_-]+)*", namespace.strip("/")):
        raise ValueError("invalid namespace")
    return f"{namespace.strip('/')}/{digest[:2]}/{digest}"


class LocalFilesystemBackend:
    name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("key escapes storage root")
        return p

    def put_stream(self, namespace: str, stream: BinaryIO) -> StoredObject:
        spool, digest, size = _spool_and_hash(stream)
        key = _key(namespace, digest)
        path = self._path(key)
        if path.exists():
            # Same key == same SHA-256 == same content; nothing to do.
            spool.close()
            return StoredObject(key, digest, size)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(spool, out, CHUNK)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(tmp, 0o440)  # read-only on disk
            os.link(tmp, path)  # atomic, fails if a concurrent writer won
        except FileExistsError:
            pass
        finally:
            spool.close()
            if os.path.exists(tmp):
                os.unlink(tmp)
        return StoredObject(key, digest, size)

    def open(self, key: str) -> BinaryIO:
        p = self._path(key)
        if not p.is_file():
            raise ObjectNotFound(key)
        return p.open("rb")

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        with self.open(key) as f:
            while chunk := f.read(CHUNK):
                yield chunk

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        p = self._path(key)
        if not p.is_file():
            raise ObjectNotFound(key)
        return p.stat().st_size

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            os.chmod(p, 0o640)
            p.unlink()

    def iter_objects(self) -> Iterator[tuple[str, int, float]]:
        for dirpath, _, files in os.walk(self.root):
            for fn in files:
                if fn.startswith("."):
                    continue  # in-flight temp files (.upload-*) and probes
                p = Path(dirpath) / fn
                try:
                    st = p.stat()
                except FileNotFoundError:
                    continue
                yield p.relative_to(self.root).as_posix(), st.st_size, st.st_mtime

    def healthcheck(self) -> None:
        probe = self.root / ".healthcheck"
        probe.write_text("ok")
        probe.unlink()


class S3Backend:
    """S3-compatible backend (MinIO, Ceph RGW, AWS S3)."""

    name = "s3"

    def __init__(self, *, bucket: str, endpoint_url: str | None, access_key: str | None, secret_key: str | None, region: str):
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put_stream(self, namespace: str, stream: BinaryIO) -> StoredObject:
        spool, digest, size = _spool_and_hash(stream)
        key = _key(namespace, digest)
        try:
            if not self.exists(key):
                self.client.upload_fileobj(spool, self.bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})
        finally:
            spool.close()
        return StoredObject(key, digest, size)

    def open(self, key: str) -> BinaryIO:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as e:
            raise ObjectNotFound(key) from e

    def iter_bytes(self, key: str) -> Iterator[bytes]:
        body = self.open(key)
        yield from body.iter_chunks(CHUNK)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def size(self, key: str) -> int:
        from botocore.exceptions import ClientError

        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as e:
            raise ObjectNotFound(key) from e

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def iter_objects(self) -> Iterator[tuple[str, int, float]]:
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
            for o in page.get("Contents", []):
                yield o["Key"], o["Size"], o["LastModified"].timestamp()

    def healthcheck(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


@lru_cache
def get_storage() -> StorageBackend:
    s = get_settings()
    if s.storage_backend == "s3":
        backend = S3Backend(
            bucket=s.s3_bucket,
            endpoint_url=s.s3_endpoint_url,
            access_key=s.s3_access_key,
            secret_key=s.s3_secret_key,
            region=s.s3_region,
        )
        backend.ensure_bucket()
        return backend
    return LocalFilesystemBackend(s.storage_local_root)
