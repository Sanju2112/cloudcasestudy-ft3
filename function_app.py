import html
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from functools import lru_cache
from urllib.parse import quote, urlsplit, urlunsplit

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, BlobSasPermissions, ContentSettings, generate_blob_sas


app = func.FunctionApp()
credential = DefaultAzureCredential()
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@lru_cache(maxsize=1)
def get_metadata_container():
    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=credential)
    return client.get_database_client(os.environ["COSMOS_DATABASE"]).get_container_client(
        os.environ["COSMOS_CONTAINER"]
    )


@lru_cache(maxsize=1)
def get_blob_service():
    return BlobServiceClient.from_connection_string(os.environ["AzureWebJobsStorage"])


def json_response(payload, status_code=200):
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
    )


def read_uploaded_file(request):
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("Send the file as multipart/form-data in a field named 'file'.")

    body = request.get_body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise OverflowError("Files must be 50 MB or smaller.")

    headers = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=policy.default).parsebytes(headers + body)
    if not message.is_multipart():
        raise ValueError("The request must contain a multipart file field named 'file'.")

    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "file":
            continue
        filename = part.get_filename()
        if not filename:
            raise ValueError("The 'file' field must include a filename.")
        filename = os.path.basename(filename.replace("\\", "/")).strip()
        if not filename:
            raise ValueError("The filename is invalid.")
        return filename, part.get_content_type(), part.get_payload(decode=True) or b""

    raise ValueError("No file was found in the multipart field named 'file'.")


def account_credentials():
    values = {}
    for item in os.environ["AzureWebJobsStorage"].split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            values[key.lower()] = value
    account_name = values.get("accountname")
    account_key = values.get("accountkey")
    if not account_name or not account_key:
        raise RuntimeError("AzureWebJobsStorage must be a storage account connection string.")
    return account_name, account_key


@app.route(route="files", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def upload_file(req: func.HttpRequest) -> func.HttpResponse:
    try:
        filename, content_type, contents = read_uploaded_file(req)
    except OverflowError as error:
        return json_response({"error": str(error)}, 413)
    except ValueError as error:
        return json_response({"error": str(error)}, 400)

    short_code = secrets.token_urlsafe(9)
    blob_name = short_code
    safe_download_name = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    container_name = os.environ["BLOB_CONTAINER_NAME"]
    blob_client = get_blob_service().get_blob_client(container=container_name, blob=blob_name)

    request_url = urlsplit(req.url)
    origin = urlunsplit((request_url.scheme, request_url.netloc, "", "", ""))
    short_url = f"{origin}/api/files/{quote(short_code)}"

    try:
        blob_client.upload_blob(
            contents,
            overwrite=False,
            content_settings=ContentSettings(
                content_type=content_type,
                content_disposition=f'attachment; filename="{safe_download_name}"',
            ),
        )
        metadata = {
            "id": short_code,
            "shortCode": short_code,
            "shortUrl": short_url,
            "fileName": filename,
            "contentType": content_type,
            "fileSize": len(contents),
            "blobName": blob_name,
            "blobUrl": blob_client.url,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        get_metadata_container().create_item(body=metadata)
    except Exception:
        logging.exception("Could not save uploaded file or its metadata")
        try:
            blob_client.delete_blob()
        except Exception:
            logging.exception("Could not remove blob after upload failure")
        return json_response({"error": "The file could not be stored."}, 500)

    return json_response({"shortUrl": short_url, "shortCode": short_code}, 201)


def read_file_metadata(short_code):
    try:
        return get_metadata_container().read_item(item=short_code, partition_key=short_code), None
    except CosmosResourceNotFoundError:
        return None, json_response({"error": "File not found."}, 404)
    except Exception:
        logging.exception("Could not look up file metadata")
        return None, json_response({"error": "The file could not be retrieved."}, 500)


def create_download_url(metadata):
    try:
        account_name, account_key = account_credentials()
        container_name = os.environ["BLOB_CONTAINER_NAME"]
        token = generate_blob_sas(
            account_name=account_name,
            account_key=account_key,
            container_name=container_name,
            blob_name=metadata["blobName"],
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        blob_url = get_blob_service().get_blob_client(
            container=container_name,
            blob=metadata["blobName"],
        ).url
        return f"{blob_url}?{token}"
    except Exception:
        logging.exception("Could not create a download link")
        return None


def format_file_size(size):
    size = int(size or 0)
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


@app.route(route="files/{shortCode}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def file_details(req: func.HttpRequest) -> func.HttpResponse:
    short_code = req.route_params.get("shortCode", "")
    metadata, error = read_file_metadata(short_code)
    if error:
        return error

    filename = html.escape(metadata.get("fileName", "Shared file"))
    file_size = metadata.get("fileSize")
    if file_size is None:
        try:
            file_size = get_blob_service().get_blob_client(
                container=os.environ["BLOB_CONTAINER_NAME"],
                blob=metadata["blobName"],
            ).get_blob_properties().size
        except Exception:
            logging.exception("Could not retrieve the size of a previously uploaded file")
            return json_response({"error": "The file details could not be retrieved."}, 404)
    size = html.escape(format_file_size(file_size))
    download_path = f"/api/files/{quote(short_code)}/download"
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f7f8fc"><title>{filename} · CloudDrop</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(ellipse at 50% 12%,#fff,#f5f6fb 78%);font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#252940}}.card{{width:min(100%,480px);padding:38px;border:1px solid #e8eaf3;border-radius:18px;background:#fff;box-shadow:0 15px 45px #252b4a0b;text-align:center}}.brand{{margin:0 0 30px;color:#645ddd;font-size:15px;font-weight:750;letter-spacing:-.4px}}.icon{{width:58px;height:58px;display:grid;place-items:center;margin:0 auto 20px;border-radius:18px;background:#f0efff;color:#6b65d7;font-size:27px}}h1{{margin:0 auto 8px;font-size:21px;line-height:1.4;overflow-wrap:anywhere}}.hint{{margin:0;color:#85899e;font-size:13px}}.meta{{display:grid;gap:13px;margin:26px 0 20px;padding:16px 0;border-top:1px solid #eeeef4;border-bottom:1px solid #eeeef4;text-align:left;font-size:12px}}.meta-row{{display:flex;justify-content:space-between;gap:16px}}.meta-row span{{color:#9296a9}}.meta-row strong{{max-width:70%;overflow-wrap:anywhere;text-align:right;font-weight:600}}.download{{width:100%;display:block;padding:13px 18px;border-radius:9px;background:#645ddd;color:white;text-decoration:none;font-weight:650;font-size:14px}}.download:hover{{background:#554ecb}}.footer{{margin:18px 0 0;color:#a0a4b5;font-size:11px}}@media(max-width:480px){{.card{{padding:30px 23px}}}}
</style></head><body><main class="card"><div class="brand">☁ CloudDrop</div><div class="icon" aria-hidden="true">↧</div><h1>{filename}</h1><p class="hint">A file has been shared with you.</p><div class="meta"><div class="meta-row"><span>File name</span><strong>{filename}</strong></div><div class="meta-row"><span>File size</span><strong>{size}</strong></div></div><a class="download" href="{download_path}">Download file</a><p class="footer">The download starts when you click the button.</p></main></body></html>"""
    return func.HttpResponse(page, status_code=200, mimetype="text/html")


@app.route(route="files/{shortCode}/download", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def download_file(req: func.HttpRequest) -> func.HttpResponse:
    short_code = req.route_params.get("shortCode", "")
    metadata, error = read_file_metadata(short_code)
    if error:
        return error

    download_url = create_download_url(metadata)
    if not download_url:
        return json_response({"error": "The download link could not be created."}, 500)

    return func.HttpResponse(
        status_code=302,
        headers={"Location": download_url},
    )
