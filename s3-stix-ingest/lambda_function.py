"""S3 to OpenCTI STIX bundle ingestion.

Triggered by S3 ObjectCreated events. Reads a STIX 2.1 bundle from the ingest
bucket and pushes it into the OpenCTI ingestion pipeline with the
stixBundlePush GraphQL mutation.

Processing is asynchronous on the platform side: the bundle is queued for the
existing OpenCTI workers, so this function returns as soon as the platform
accepts it. The connector container itself is never involved; only the
connector's registration and its push queue are used.

No third-party packages are required. boto3 ships with the Lambda runtime and
everything else comes from the standard library.

Environment variables:
  OPENCTI_URL       Internal platform URL, e.g. http://platform.opencti.local:4000
  CONNECTOR_ID      UUID of an existing registered connector (ImportFileStix is fine)
  TOKEN_SECRET_ARN  Secrets Manager ARN holding the OpenCTI API token
  TOKEN_JSON_KEY    JSON key inside that secret (default: token)
  MAX_BUNDLE_BYTES  Reject objects larger than this (default: 50 MiB)
  HTTP_TIMEOUT      GraphQL request timeout in seconds (default: 60)
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
secretsmanager = boto3.client("secretsmanager")

OPENCTI_URL = os.environ["OPENCTI_URL"].rstrip("/")
CONNECTOR_ID = os.environ["CONNECTOR_ID"]
TOKEN_SECRET_ARN = os.environ["TOKEN_SECRET_ARN"]
TOKEN_JSON_KEY = os.environ.get("TOKEN_JSON_KEY", "token")
MAX_BUNDLE_BYTES = int(os.environ.get("MAX_BUNDLE_BYTES", 50 * 1024 * 1024))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "60"))

STIX_BUNDLE_PUSH = """
mutation StixBundlePush($connectorId: String!, $bundle: String!) {
  stixBundlePush(connectorId: $connectorId, bundle: $bundle)
}
"""

_token_cache = None


def _get_token():
    """Return the OpenCTI API token, cached for the life of the container."""
    global _token_cache
    if _token_cache is None:
        secret = secretsmanager.get_secret_value(SecretId=TOKEN_SECRET_ARN)
        _token_cache = json.loads(secret["SecretString"])[TOKEN_JSON_KEY]
    return _token_cache


def _push_bundle(bundle):
    """Send one STIX bundle to the platform, raising on any failure."""
    payload = json.dumps(
        {
            "query": STIX_BUNDLE_PUSH,
            "variables": {"connectorId": CONNECTOR_ID, "bundle": bundle},
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{OPENCTI_URL}/graphql",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_get_token()}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"OpenCTI returned HTTP {exc.code}: {detail}") from exc

    # GraphQL reports application errors in a 200 response, so check the body.
    if body.get("errors"):
        raise RuntimeError(f"OpenCTI GraphQL error: {json.dumps(body['errors'])[:500]}")
    if body.get("data", {}).get("stixBundlePush") is not True:
        raise RuntimeError(f"stixBundlePush did not succeed: {json.dumps(body)[:500]}")


def handler(event, context):
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        size = record["s3"]["object"].get("size", 0)

        if size > MAX_BUNDLE_BYTES:
            raise RuntimeError(
                f"s3://{bucket}/{key} is {size} bytes, above MAX_BUNDLE_BYTES "
                f"({MAX_BUNDLE_BYTES}). Split the bundle before uploading."
            )

        logger.info("Reading s3://%s/%s (%s bytes)", bucket, key, size)
        bundle = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")

        try:
            parsed = json.loads(bundle)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"s3://{bucket}/{key} is not valid JSON: {exc}") from exc

        if parsed.get("type") != "bundle":
            raise RuntimeError(
                f"s3://{bucket}/{key} is not a STIX bundle (type={parsed.get('type')!r})"
            )

        object_count = len(parsed.get("objects", []))
        if object_count == 0:
            logger.warning("s3://%s/%s has no objects; skipping", bucket, key)
            continue

        _push_bundle(bundle)
        logger.info(
            "Queued %s objects from s3://%s/%s for ingestion", object_count, bucket, key
        )

    return {"status": "ok"}
