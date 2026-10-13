"""End-to-end smoke test for the deployed CloudDrop upload and download APIs."""

import json
import os
import secrets
import unittest
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen


API_BASE_URL = os.environ.get(
    "CLOUDDROP_API_BASE_URL",
    "https://clouddrop-fn-a1b2c3.azurewebsites.net",
).rstrip("/")


class DownloadLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.download_path = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a" and "download" in attributes.get("class", "").split():
            self.download_path = attributes.get("href")


class CloudDropApiTest(unittest.TestCase):
    def test_upload_then_download(self):
        boundary = "----CloudDropTest" + secrets.token_hex(8)
        payload = b"CloudDrop API smoke test\n"
        multipart_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="clouddrop-smoke-test.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("utf-8")

        upload_request = Request(
            f"{API_BASE_URL}/api/files",
            data=multipart_body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(upload_request, timeout=60) as response:
            self.assertEqual(response.status, 201)
            upload_result = json.loads(response.read())

        short_url = upload_result.get("shortUrl")
        self.assertTrue(short_url, "Upload response did not include shortUrl")
        self.assertTrue(upload_result.get("shortCode"), "Upload response did not include shortCode")

        # The short URL now shows file details; the page's button starts the download.
        with urlopen(short_url, timeout=60) as response:
            self.assertEqual(response.status, 200)
            details_page = response.read().decode("utf-8")
        self.assertIn("clouddrop-smoke-test.txt", details_page)
        self.assertIn(f"{len(payload)} bytes", details_page)
        parser = DownloadLinkParser()
        parser.feed(details_page)
        self.assertTrue(parser.download_path, "Details page did not include a download button")

        # urllib follows the download endpoint's redirect to the private blob SAS URL.
        with urlopen(urljoin(short_url, parser.download_path), timeout=60) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
