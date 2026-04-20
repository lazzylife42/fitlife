import sqlite3
import json
import requests
from datetime import datetime, timedelta

DB_PATH = '/data/fitlife.db'


def get_google_token():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM oauth_tokens WHERE provider='google'").fetchone()
    db.close()
    if not row:
        raise Exception("Google token not found in DB")
    expires_at = datetime.fromisoformat(row['expires_at'])
    if datetime.utcnow() >= expires_at - timedelta(minutes=5):
        raw = json.loads(row['raw'])
        google_client_id = raw.get('client_id') or input("Google Client ID: ")
        google_client_secret = raw.get('client_secret') or input("Google Client Secret: ")
        resp = requests.post('https://oauth2.googleapis.com/token', data={
            'client_id': google_client_id,
            'client_secret': google_client_secret,
            'refresh_token': row['refresh_token'],
            'grant_type': 'refresh_token',
        })
        if not resp.ok:
            raise Exception(f"Token refresh failed: {resp.text}")
        return resp.json()['access_token']
    return row['access_token']


def delete_fitlife_events(token):
    deleted = 0
    page_token = None
    while True:
        params = {
            'maxResults': 250,
            'q': 'FitLife',
            'singleEvents': True,
        }
        if page_token:
            params['pageToken'] = page_token
        resp = requests.get(
            'https://www.googleapis.com/calendar/v3/calendars/primary/events',
            headers={'Authorization': f'Bearer {token}'},
            params=params,
        )
        if not resp.ok:
            print(f"Error listing events: {resp.text}")
            break
        data = resp.json()
        events = data.get('items', [])
        for event in events:
            summary = event.get('summary', '')
            if summary.startswith('FitLife'):
                event_id = event['id']
                del_resp = requests.delete(
                    f'https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}',
                    headers={'Authorization': f'Bearer {token}'},
                )
                if del_resp.status_code in (200, 204):
                    print(f"Deleted: {summary} ({event.get('start', {}).get('dateTime', '?')})")
                    deleted += 1
                else:
                    print(f"Failed to delete {event_id}: {del_resp.text}")
        page_token = data.get('nextPageToken')
        if not page_token:
            break
    print(f"\nTotal deleted: {deleted}")


if __name__ == '__main__':
    token = get_google_token()
    delete_fitlife_events(token)
