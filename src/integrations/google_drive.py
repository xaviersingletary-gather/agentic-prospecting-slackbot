import json
import logging
import os
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

DRIVE_SEARCH_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{file_id}/export"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _load_service_account() -> Optional[dict]:
    path = settings.GOOGLE_SERVICE_ACCOUNT_JSON_PATH
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[drive] Could not load service account JSON: {e}")
        return None


def _get_access_token(sa: dict) -> Optional[str]:
    """Exchange service account credentials for a short-lived OAuth2 access token."""
    try:
        import time
        import base64

        # Build JWT header + claim
        now = int(time.time())
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()

        claim = base64.urlsafe_b64encode(
            json.dumps({
                "iss": sa["client_email"],
                "scope": " ".join(SCOPES),
                "aud": TOKEN_URL,
                "exp": now + 3600,
                "iat": now,
            }).encode()
        ).rstrip(b"=").decode()

        signing_input = f"{header}.{claim}".encode()

        # Sign with the private key
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = serialization.load_pem_private_key(
            sa["private_key"].encode(), password=None
        )
        signature = base64.urlsafe_b64encode(
            private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        ).rstrip(b"=").decode()

        jwt_token = f"{header}.{claim}.{signature}"

        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt_token,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("access_token")

    except Exception as e:
        logger.warning(f"[drive] Token exchange failed: {e}")
        return None


class GoogleDriveClient:
    def __init__(self):
        self._sa = _load_service_account()
        self._token: Optional[str] = None

    @property
    def configured(self) -> bool:
        return self._sa is not None

    def _auth_header(self) -> dict:
        if not self._token:
            self._token = _get_access_token(self._sa) if self._sa else None
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def find_account_plan(self, account_name: str) -> Optional[str]:
        """
        Search Drive for a Google Doc or file containing the account name.
        Returns extracted plain text, or None if not found / not configured.
        """
        if not self.configured:
            logger.info("[drive] Not configured — skipping account plan fetch")
            return None

        try:
            folder_id = settings.GOOGLE_DRIVE_ACCOUNT_PLANS_FOLDER_ID
            query_parts = [f"name contains '{account_name}'", "trashed = false"]
            if folder_id:
                query_parts.append(f"'{folder_id}' in parents")

            response = httpx.get(
                DRIVE_SEARCH_URL,
                headers=self._auth_header(),
                params={
                    "q": " and ".join(query_parts),
                    "fields": "files(id,name,mimeType)",
                    "pageSize": 1,
                    "orderBy": "modifiedTime desc",
                },
                timeout=10,
            )
            response.raise_for_status()
            files = response.json().get("files", [])

            if not files:
                logger.info(f"[drive] No account plan found for '{account_name}'")
                return None

            file = files[0]
            logger.info(f"[drive] Found account plan: {file['name']}")
            return self._export_as_text(file["id"], file["mimeType"])

        except Exception as e:
            logger.warning(f"[drive] find_account_plan failed for '{account_name}': {e}")
            return None

    def _export_as_text(self, file_id: str, mime_type: str) -> Optional[str]:
        """Export a Drive file as plain text."""
        try:
            if "google-apps.document" in mime_type:
                response = httpx.get(
                    DRIVE_EXPORT_URL.format(file_id=file_id),
                    headers=self._auth_header(),
                    params={"mimeType": "text/plain"},
                    timeout=15,
                )
            else:
                # Regular file — download directly
                response = httpx.get(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}",
                    headers=self._auth_header(),
                    params={"alt": "media"},
                    timeout=15,
                )
            response.raise_for_status()
            text = response.text[:8000]  # cap at 8k chars to keep LLM context reasonable
            logger.info(f"[drive] Exported {len(text)} chars from file {file_id}")
            return text

        except Exception as e:
            logger.warning(f"[drive] Export failed for {file_id}: {e}")
            return None
