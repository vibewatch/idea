"""GitHub API helpers for encrypted Actions secrets and refresh reports."""

from __future__ import annotations

import base64

import requests
from nacl import encoding, public

_HEADERS_TEMPLATE = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _headers(token: str) -> dict[str, str]:
    return {**_HEADERS_TEMPLATE, "Authorization": f"Bearer {token}"}


def _encrypt_secret(public_key: str, secret_value: str) -> str:
    key = public.PublicKey(public_key.encode(), encoding.Base64Encoder())
    encrypted = public.SealedBox(key).encrypt(secret_value.encode())
    return base64.b64encode(encrypted).decode()


def _validate_repo(repo: str) -> None:
    owner, separator, name = repo.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise ValueError("GITHUB_REPOSITORY must use the 'owner/repository' format")


def get_repo_public_key(repo: str, token: str) -> tuple[str, str]:
    """Get the key GitHub requires for Actions secret encryption."""
    _validate_repo(repo)
    response = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=_headers(token),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return str(data["key"]), str(data["key_id"])


def update_secret(repo: str, token: str, secret_name: str, secret_value: str) -> None:
    """Encrypt and replace one repository Actions secret."""
    public_key, key_id = get_repo_public_key(repo, token)
    response = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}",
        headers=_headers(token),
        json={
            "encrypted_value": _encrypt_secret(public_key, secret_value),
            "key_id": key_id,
        },
        timeout=30,
    )
    response.raise_for_status()


def create_issue(
    repo: str,
    token: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> str:
    """Create a refresh report, retrying without labels when they do not exist."""
    _validate_repo(repo)
    url = f"https://api.github.com/repos/{repo}/issues"
    payload: dict[str, object] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    response = requests.post(url, headers=_headers(token), json=payload, timeout=30)
    if response.status_code == 422 and labels:
        response = requests.post(
            url,
            headers=_headers(token),
            json={"title": title, "body": body},
            timeout=30,
        )
    response.raise_for_status()
    return str(response.json()["html_url"])
