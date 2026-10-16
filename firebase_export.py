r"""Firestore -> JSON exporter (read-only) for the JardX migration.

Uses the Firestore REST API with a service-account key. No extra packages
needed (google-auth + requests). Output goes to Backend/firebase_dump/firestore/.

Usage:
  python firebase_export.py                         # export all collections
  python firebase_export.py --collections users,transactions
  python firebase_export.py --key path\to\key.json --project jardx-e2556
"""
import argparse
import json
import time
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_KEY = r"C:\Users\NEW USER\Downloads\jardx-e2556-firebase-adminsdk-fbsvc-98eaa0989a.json"
DEFAULT_PROJECT = "jardx-e2556"
OUT_DIR = BASE_DIR / "firebase_dump" / "firestore"
SCOPES = ["https://www.googleapis.com/auth/datastore"]


def get_token(key_path):
    creds = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)
    creds.refresh(Request())
    return {"Authorization": "Bearer " + creds.token}


def request_with_retry(session, method, url, headers, tries=5, **kwargs):
    last = None
    for attempt in range(tries):
        try:
            r = session.request(method, url, headers=headers, timeout=90, **kwargs)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                last = r
                continue
            return r
        except Exception as e:  # network hiccup
            last = e
            time.sleep(2 * (attempt + 1))
    if isinstance(last, Exception):
        raise last
    return last


def to_python(value):
    """Convert a Firestore Value object into a plain Python value."""
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        try:
            return int(value["integerValue"])
        except (TypeError, ValueError):
            return value["integerValue"]
    if "doubleValue" in value:
        return value["doubleValue"]
    if "booleanValue" in value:
        return value["booleanValue"]
    if "timestampValue" in value:
        return value["timestampValue"]
    if "nullValue" in value:
        return None
    if "referenceValue" in value:
        return value["referenceValue"]
    if "geoPointValue" in value:
        return value["geoPointValue"]
    if "bytesValue" in value:
        return value["bytesValue"]
    if "arrayValue" in value:
        return [to_python(v) for v in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return {k: to_python(v) for k, v in value["mapValue"].get("fields", {}).items()}
    return value


def doc_to_dict(doc):
    doc_id = doc["name"].rsplit("/", 1)[-1]
    fields = {k: to_python(v) for k, v in doc.get("fields", {}).items()}
    fields["_id"] = doc_id
    return fields


def list_collection_ids(session, headers, project):
    url = "https://firestore.googleapis.com/v1/projects/%s/databases/(default)/documents:listCollectionIds" % project
    r = request_with_retry(session, "POST", url, headers, json={"pageSize": 300})
    r.raise_for_status()
    return r.json().get("collectionIds", [])


def dump_collection(session, headers, project, collection):
    base = "https://firestore.googleapis.com/v1/projects/%s/databases/(default)/documents/%s" % (project, collection)
    docs = []
    token = None
    while True:
        params = {"pageSize": 300}
        if token:
            params["pageToken"] = token
        r = request_with_retry(session, "GET", base, headers, params=params)
        r.raise_for_status()
        data = r.json()
        for d in data.get("documents", []):
            docs.append(doc_to_dict(d))
        token = data.get("nextPageToken")
        if not token:
            break
    return docs


def main():
    parser = argparse.ArgumentParser(description="Export Firestore collections to JSON")
    parser.add_argument("--key", default=DEFAULT_KEY)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--collections", default="all",
                        help="comma-separated collection names, or 'all'")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    headers = get_token(args.key)
    session = requests.Session()

    if args.collections.strip().lower() == "all":
        collections = list_collection_ids(session, headers, args.project)
    else:
        collections = [c.strip() for c in args.collections.split(",") if c.strip()]

    manifest = {}
    for c in collections:
        docs = dump_collection(session, headers, args.project, c)
        (OUT_DIR / ("%s.json" % c)).write_text(
            json.dumps(docs, ensure_ascii=False, default=str), encoding="utf-8")
        manifest[c] = len(docs)
        print("exported %-28s %6d docs" % (c, len(docs)))

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("DONE ->", OUT_DIR)


if __name__ == "__main__":
    main()
