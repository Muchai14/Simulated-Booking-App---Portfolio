"""
One-off import of SIMULATED events (events.jsonl) into Mixpanel via the /import API.

Credentials are read from environment variables ONLY. Never hard-code them and
never commit them to GitHub.

Option A: Service account (recommended by Mixpanel docs)
    export MIXPANEL_PROJECT_ID="..."
    export MIXPANEL_SA_USER="..."
    export MIXPANEL_SA_SECRET="..."

Option B: Project token (the docs say /import also accepts it as the basic-auth
username with an empty password)
    export MIXPANEL_PROJECT_TOKEN="..."

Optional:
    export MIXPANEL_REGION="api"      # "api" (US, default), "api-eu", or "api-in"

Usage:
    python import_to_mixpanel.py --dry-run     # parse and count only, no network
    python import_to_mixpanel.py --limit 100   # send only the first 100 events (test)
    python import_to_mixpanel.py               # send everything

Docs: https://docs.mixpanel.com/reference/import-events
(Check them for the latest limits: at the time of writing, up to 2000 events per
request, $insert_id required, strict=1 recommended.)
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BATCH_SIZE = 1000  # docs allow up to 2000 events per request; stay below it


def build_request(batch, region, use_service_account, project_id, user, secret):
    params = {"strict": "1"}
    if use_service_account:
        params["project_id"] = project_id
    url = f"https://{region}.mixpanel.com/import?{urllib.parse.urlencode(params)}"
    token = base64.b64encode(f"{user}:{secret}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=json.dumps(batch).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
        },
        method="POST",
    )
    return req


def read_events(path, limit=None):
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="events.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    events = read_events(args.file, args.limit)
    print(f"Read {len(events):,} events from {args.file}")

    # Basic sanity checks on the events themselves
    missing = [e for e in events
               if not e.get("properties", {}).get("$insert_id")
               or not e.get("properties", {}).get("distinct_id")
               or not e.get("properties", {}).get("time")]
    print(f"Events missing $insert_id / distinct_id / time: {len(missing)}")
    if missing:
        sys.exit("Fix the events before importing.")

    if args.dry_run:
        print("Dry run: nothing was sent.")
        return

    region = os.environ.get("MIXPANEL_REGION", "api")
    project_id = os.environ.get("MIXPANEL_PROJECT_ID")
    sa_user = os.environ.get("MIXPANEL_SA_USER")
    sa_secret = os.environ.get("MIXPANEL_SA_SECRET")
    project_token = os.environ.get("MIXPANEL_PROJECT_TOKEN")

    if project_id and sa_user and sa_secret:
        use_sa, user, secret = True, sa_user, sa_secret
        print("Auth: service account")
    elif project_token:
        use_sa, user, secret = False, project_token, ""
        print("Auth: project token")
    else:
        sys.exit("No credentials found in environment variables. See the docstring at the top of this file.")

    imported, failed_batches = 0, 0
    for start in range(0, len(events), BATCH_SIZE):
        batch = events[start:start + BATCH_SIZE]
        req = build_request(batch, region, use_sa, project_id, user, secret)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
                n = body.get("num_records_imported", len(batch))
                imported += n
                print(f"Batch {start // BATCH_SIZE + 1}: HTTP {resp.status}, imported {n}")
        except urllib.error.HTTPError as e:
            failed_batches += 1
            detail = e.read().decode("utf-8", errors="replace")[:500]
            print(f"Batch {start // BATCH_SIZE + 1}: HTTP {e.code} -> {detail}")
        except urllib.error.URLError as e:
            failed_batches += 1
            print(f"Batch {start // BATCH_SIZE + 1}: network error -> {e.reason}")

    print(f"\nDone. Imported: {imported:,} / {len(events):,}   Failed batches: {failed_batches}")
    print("In Mixpanel, set the date range to Jul-Sep 2026 to see the data.")


if __name__ == "__main__":
    main()
