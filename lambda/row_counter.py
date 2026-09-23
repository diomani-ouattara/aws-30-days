"""Day 13: log the size, row count and column count of every Parquet file landing in raw/.

Trigger : S3 ObjectCreated, prefix raw/, suffix .parquet
Runtime : Python 3.14, 512 MB, timeout 60 s
Layer   : AWSSDKPandas-Python314 (AWS-managed; ships pandas + pyarrow)

A bare Lambda has boto3 and the standard library - nothing else. pyarrow comes from the
managed layer, which is the cheap way to get scientific packages in without building anything.
(The other way, a container image, is Day 24.)

The layer's pyarrow is compiled WITHOUT S3FileSystem support to keep it small, so
`pyarrow.fs.S3FileSystem` raises on import. S3RangeReader below is the way around it:
a minimal seekable file object that fetches only the byte ranges pyarrow asks for.
Reading the footer of a 50 MB file costs ~2 small GETs instead of a 50 MB download -
which matters, because Lambda bills GB-seconds.
"""
import io
import logging
import urllib.parse

import boto3
import pyarrow.parquet as pq

log = logging.getLogger()
log.setLevel(logging.INFO)

s3 = boto3.client("s3")


class S3RangeReader(io.RawIOBase):
    """Seekable read-only file over an S3 object, one HTTP range request per read()."""

    def __init__(self, bucket, key):
        self._bucket, self._key = bucket, key
        self._pos = 0
        self._size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = max(0, min(self._size, base + offset))
        return self._pos

    def read(self, size=-1):
        if size is None or size < 0:
            size = self._size - self._pos
        size = min(size, self._size - self._pos)
        if size <= 0:
            return b""
        end = self._pos + size - 1
        body = s3.get_object(
            Bucket=self._bucket, Key=self._key, Range=f"bytes={self._pos}-{end}"
        )["Body"].read()
        self._pos += len(body)
        return body

    def readinto(self, b):
        """BufferedReader calls this, not read()."""
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def lambda_handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        size_mb = record["s3"]["object"]["size"] / 1e6

        if not key.endswith(".parquet"):
            log.info("SKIP    %s  (%.1f MB) - not parquet", key, size_mb)
            continue

        with io.BufferedReader(S3RangeReader(bucket, key)) as f:
            md = pq.read_metadata(f)

        log.info(
            "LANDED  %s  %.1f MB  %s rows  %d cols  %d row groups",
            key, size_mb, f"{md.num_rows:,}", md.num_columns, md.num_row_groups,
        )

    return {"statusCode": 200, "files": len(event["Records"])}
