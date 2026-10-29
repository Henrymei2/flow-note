# Sharing research sessions

## Private Cloudflare R2 sharing

Copy `.env.example` to `.env` and add a bucket-scoped R2 Access Key ID and Secret Access Key. Keep
the bucket private. Give uploaders an **Object Read & Write** token scoped to this bucket, and give
researchers who only download an **Object Read** token. Each researcher puts their own token in the
same `.env` fields on their local device. 

With R2 configured, **Upload to cloud**:

1. Builds the same checksummed research ZIP used by the local download, including all available sources.
2. Uploads it under `sessions/<session-id>/<sha256>.zip`
3. Uses multipart upload for archives of 100 MB or more.
4. Publishes a small metadata record under `catalog/<session-id>.json`.
5. Returns a private presigned download link with a configurable expiry of no more than seven days.

Use **Download archive** on a finished or interrupted session to download one portable ZIP. The archive
can contain:

- `media/video.webm`: the FL Studio window recording and any captured audio.
- `physiology/*.csv`: one signal per file, with `timeline_seconds,value` columns.
- `events/events.csv`: FL Studio event log.
- `events/events.jsonl`: same event log with nested payloads preserved.
- `manifest.json`: session metadata, time alignment, format version, and SHA-256 checksums.
- `README.txt`: data dictionary and import notes.

To install the same recorder, choose **Import research archive**, and select the
ZIP.
