"""Download Earth Engine export CSVs from Google Drive without knowing which account they landed in.

    python3 scripts/fetch_drive_exports.py --folder SCN_GEE_AlphaEarth_v2 \
        --dest "/Users/nqj5zk/Library/CloudStorage/OneDrive-UniversityofVirginia/data/gia/SCN_GEE_AlphaEarth_v2"

Uses the credentials saved by `earthengine authenticate` (the account Earth Engine exported to).
If that token lacks Drive permission, the script says so and tells you the one-time fix.
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path


def drive_service():
    try:
        import ee  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit(f"Missing package ({exc.name}). Run: python3 -m pip install earthengine-api google-api-python-client") from exc
    creds = ee.data.get_persistent_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", required=True, help="Drive folder name Earth Engine exported into")
    ap.add_argument("--dest", required=True, type=Path, help="Local folder to copy the files into")
    ap.add_argument("--pattern", default="aef_", help="Only files whose name starts with this")
    args = ap.parse_args()

    from googleapiclient.errors import HttpError  # type: ignore
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore

    svc = drive_service()
    try:
        me = svc.about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            raise SystemExit(
                "The Earth Engine token does not include Google Drive access.\n"
                "One-time fix, then rerun this script:\n"
                "  earthengine authenticate --scopes https://www.googleapis.com/auth/earthengine,"
                "https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/cloud-platform\n"
                "Sign in with the SAME Google account you used before (the one Earth Engine exported to)."
            ) from exc
        raise
    print(f"Signed in to Drive as {me}")

    q = f"name = '{args.folder}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    folders = svc.files().list(q=q, fields="files(id, name, createdTime)").execute()["files"]
    if not folders:
        raise SystemExit(f"No folder named {args.folder!r} in {me}'s Drive. Check the Earth Engine Tasks tab for the destination.")
    if len(folders) > 1:
        print(f"  {len(folders)} folders with that name; using the newest")
    folder = sorted(folders, key=lambda f: f["createdTime"])[-1]

    q = f"'{folder['id']}' in parents and trashed = false and name contains '{args.pattern}'"
    files, token = [], None
    while True:
        resp = svc.files().list(q=q, fields="nextPageToken, files(id, name, size, modifiedTime)", pageToken=token).execute()
        files += resp["files"]
        token = resp.get("nextPageToken")
        if not token:
            break
    if not files:
        raise SystemExit(f"Folder {args.folder} exists but has no files starting with {args.pattern!r}")

    args.dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(files, key=lambda f: f["name"]):
        target = args.dest / f["name"]
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, svc.files().get_media(fileId=f["id"]))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = buf.getvalue()
        if int(f.get("size", len(data))) != len(data):
            raise RuntimeError(f"{f['name']}: downloaded {len(data)} bytes, Drive reports {f['size']}")
        target.write_bytes(data)
        print(f"  {f['name']}  {len(data) / 1e6:.2f} MB")
    print(f"{len(files)} files -> {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
