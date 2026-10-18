import boto3
from botocore.config import Config

from app.core.settings import settings

# Neon object storage needs path-style; region comes from AWS_DEFAULT_REGION.
_s3 = boto3.client(
    "s3", endpoint_url=settings.s3_endpoint_url or None,
    config=Config(s3={"addressing_style": "path"}, connect_timeout=10, read_timeout=60, retries={"max_attempts": 3, "mode": "standard"}))


def put_private(key: str, data: bytes) -> None:
    _s3.put_object(Bucket=settings.archive_bucket, Key=key, Body=data)


def get_private(key: str) -> bytes:
    return _s3.get_object(Bucket=settings.archive_bucket, Key=key)["Body"].read()


def put_public(prefix: str, name: str, data: bytes, content_type: str) -> str:
    key = f"{prefix.strip('/')}/{name}"
    _s3.put_object(Bucket=settings.cdn_bucket, Key=key, Body=data, ContentType=content_type)
    return f"{settings.cdn_base_url.rstrip('/')}/{key}"
