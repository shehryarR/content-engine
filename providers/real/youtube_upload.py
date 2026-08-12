"""
providers/youtube_upload.py

Real YouTube upload adapter. S100 will eventually call this instead of
the dry-run-only stub. Supports dry_run=True (validate + mock receipt)
and dry_run=False (real API call, once the audit clears).
"""

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from contracts.stages.s100_publish import PublishReceiptV1
from scripts.youtube_auth import get_credentials


def upload_video(
    video_path: str,
    title: str,
    description: str,
    privacy_status: str,
    contains_synthetic_media: bool,
    run_id: str,
    dry_run: bool = True,
) -> PublishReceiptV1:
    if not contains_synthetic_media:
        raise ValueError("Refusing to upload: containsSyntheticMedia is False")
    if privacy_status not in ("unlisted", "private"):
        raise ValueError(f"Invalid privacy status: {privacy_status}")
    if privacy_status == "public":
        raise ValueError("Public uploads are not permitted from this pipeline")

    if dry_run:
        print(f"[DRY RUN] Would upload {video_path} as {privacy_status}")
        return PublishReceiptV1(
            run_id=run_id,
            platform_video_id=None,
            privacy=privacy_status,
            dry_run=True,
        )

    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "selfDeclaredMadeForKids": False,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfCertification": {
                    "containsSyntheticMedia": contains_synthetic_media
                },
            },
        },
        media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True),
    )
    response = request.execute()

    return PublishReceiptV1(
        run_id=run_id,
        platform_video_id=response["id"],
        privacy=privacy_status,
        dry_run=False,
    )
