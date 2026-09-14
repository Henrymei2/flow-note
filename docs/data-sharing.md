# Sharing research sessions

Use **Download archive** on a finished session to download one portable ZIP. The recorder only
enables a complete share when both the video and physiological recording are ready. The archive
contains:

- `media/video.webm`: the FL Studio window recording and any captured audio.
- `physiology/*.csv`: one signal per file, with `timeline_seconds,value` columns.
- `events/events.csv`: a table-friendly FL Studio event log.
- `events/events.jsonl`: the same event log with nested payloads preserved.
- `manifest.json`: session metadata, time alignment, format version, and SHA-256 checksums.
- `README.txt`: a short data dictionary and import notes.

Another researcher can install the same recorder, choose **Import research archive**, and select the
ZIP. The app verifies all required checksums before creating a local session, then restores video,
signals, events, and their shared timeline. If that source session is already present, the import is
given a new local ID so existing data is never overwritten.

## Private Cloudflare R2 sharing

Copy `.env.example` to `.env` and add a bucket-scoped R2 Access Key ID and Secret Access Key. Keep
the bucket private. Give uploaders an **Object Read & Write** token scoped to this bucket, and give
researchers who only download an **Object Read** token. Each researcher puts their own token in the
same `.env` fields on their local device. The `.env` file is ignored by Git, installed with owner-only
permissions on macOS, and the dashboard never returns either credential to the browser.

With R2 configured, **Upload to cloud**:

1. Builds the same complete, checksummed research ZIP used by the local download.
2. Uploads it under `sessions/<session-id>/<sha256>.zip`, making the object name content-addressed.
3. Uses multipart upload for archives of 100 MB or more.
4. Publishes a small metadata record under `catalog/<session-id>.json`.
5. Returns a private presigned download link with a configurable expiry of no more than seven days.

The refresh button reloads both local sessions and this R2 catalog. A cloud-session entry creates a
fresh, temporary download URL only when the researcher clicks it. After downloading the ZIP, they
import it through the dashboard as usual. Researchers therefore need read access to the bucket; they
do not need the uploaders' write-capable token. Presigned URLs are bearer credentials: anyone who
receives one can download the object until it expires.

## Recommended research practice

1. Stop the session cleanly and confirm that Video and EmotiBit both show `ready` before export.
2. Keep the ZIP intact. Do not rename or edit files inside it; doing so intentionally fails checksum
   verification. Use a copied, extracted dataset for analysis.
3. Record a participant pseudonym and study metadata outside project or track names. The archive can
   contain visible or audible identifying information as well as sensitive physiology.
4. Use an institution-approved encrypted transfer and storage service. Give access only to named
   study personnel, set an expiry where possible, and never send the decryption secret in the same
   channel as the archive.
5. Retain the untouched ZIP as the immutable source package. Create analysis outputs separately and
   document the recorder version, archive checksum, code version, exclusions, and transformations.
6. Use `timeline_seconds` as the join key. Video time zero corresponds to
   `video.start_offset_seconds` on that timeline; subtract the offset to seek into the video.
7. Agree on consent, ethics/IRB approval, de-identification, retention, deletion, and breach handling
   before collection. A checksum proves integrity, not consent or confidentiality.

CSV provides a low-friction path into R, Python/pandas, MATLAB, Julia, and spreadsheet tools. JSON
Lines should be treated as the authoritative FL Studio event representation when analysis needs the
nested `payload` field.

Further guidance: the [FAIR Guiding Principles](https://doi.org/10.1038/sdata.2016.18) cover reusable
metadata and provenance, while the [NIH controlled-access guidance](https://sharing.nih.gov/data-management-and-sharing-policy/protecting-participant-privacy-when-sharing-scientific-data/designating-scientific-data-for-controlled-access)
helps determine when participant data should not be distributed through open access.
