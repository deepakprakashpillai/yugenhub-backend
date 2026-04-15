import boto3
from config import config
from logging_config import get_logger

logger = get_logger("r2")


def get_r2_client():
    """Returns a boto3 S3 client pointed at the Cloudflare R2 endpoint."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def generate_presigned_put_url(r2_key: str, content_type: str, expires_in: int = 3600) -> str:
    """Returns a presigned PUT URL for direct browser upload to R2."""
    client = get_r2_client()
    url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": config.R2_BUCKET_NAME,
            "Key": r2_key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )
    return url


def generate_presigned_get_url(r2_key: str, expires_in: int = 300) -> str:
    """Returns a presigned GET URL for time-limited file downloads from R2."""
    client = get_r2_client()
    url = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": config.R2_BUCKET_NAME,
            "Key": r2_key,
        },
        ExpiresIn=expires_in,
    )
    return url


def download_r2_object(r2_key: str) -> bytes:
    """Downloads an object from R2 and returns its bytes."""
    client = get_r2_client()
    response = client.get_object(Bucket=config.R2_BUCKET_NAME, Key=r2_key)
    return response["Body"].read()


def upload_r2_object(r2_key: str, body: bytes, content_type: str):
    """Uploads bytes directly to R2."""
    client = get_r2_client()
    client.put_object(
        Bucket=config.R2_BUCKET_NAME,
        Key=r2_key,
        Body=body,
        ContentType=content_type,
    )
    logger.info(f"Uploaded R2 object: {r2_key}")


def copy_r2_object(src_key: str, dst_key: str):
    """Copies an object within R2 (server-side copy — no download/upload)."""
    client = get_r2_client()
    client.copy_object(
        Bucket=config.R2_BUCKET_NAME,
        CopySource={"Bucket": config.R2_BUCKET_NAME, "Key": src_key},
        Key=dst_key,
    )
    logger.info(f"Copied R2 object: {src_key} → {dst_key}")


def delete_r2_object(r2_key: str):
    """Deletes an object from R2."""
    try:
        client = get_r2_client()
        client.delete_object(Bucket=config.R2_BUCKET_NAME, Key=r2_key)
    except Exception as e:
        logger.error(f"Failed to delete R2 object {r2_key}: {e}")
