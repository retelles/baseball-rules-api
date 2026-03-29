import logging
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# Cloudflare R2 is S3-compatible. Endpoint format:
# https://<account_id>.r2.cloudflarestorage.com
R2_ENDPOINT_URL = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


class StorageService:
    def __init__(self) -> None:
        self._client = None
        self.bucket = settings.R2_BUCKET_NAME

    @property
    def client(self):
        if self._client is None:
            if not settings.R2_ACCOUNT_ID:
                raise RuntimeError("R2 storage is not configured. Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET_NAME.")
            endpoint_url = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
                region_name="auto",
            )
        return self._client

    def upload_file(self, file_bytes: bytes, filename: str, content_type: str = "application/pdf") -> str:
        """Upload file bytes to R2 and return the storage path (object key)."""
        storage_path = f"rules/{filename}"
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=storage_path,
                Body=file_bytes,
                ContentType=content_type,
            )
            logger.info("Uploaded %s to R2 bucket %s", storage_path, self.bucket)
            return storage_path
        except ClientError as exc:
            logger.error("R2 upload failed for %s: %s", filename, exc)
            raise RuntimeError(f"File upload failed: {exc}") from exc

    def get_file_bytes(self, storage_path: str) -> bytes:
        """Download file bytes directly from R2."""
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=storage_path)
            return response["Body"].read()
        except Exception as exc:
            logger.error("Failed to download %s from R2: %s", storage_path, exc)
            raise RuntimeError(f"Could not download file: {exc}") from exc

    def get_download_url(self, storage_path: str, expiry_seconds: int = 3600) -> str:
        """Generate a presigned download URL valid for expiry_seconds (default 1 hour)."""
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": storage_path},
                ExpiresIn=expiry_seconds,
            )
            return url
        except Exception as exc:
            logger.error("Failed to generate presigned URL for %s: %s", storage_path, exc)
            raise RuntimeError(f"Could not generate download URL: {exc}") from exc

    def delete_file(self, storage_path: str) -> None:
        """Delete an object from R2."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=storage_path)
            logger.info("Deleted %s from R2 bucket %s", storage_path, self.bucket)
        except ClientError as exc:
            logger.error("R2 delete failed for %s: %s", storage_path, exc)
            raise RuntimeError(f"File deletion failed: {exc}") from exc


storage_service = StorageService()
