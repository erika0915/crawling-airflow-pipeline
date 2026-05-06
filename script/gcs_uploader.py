import os
from google.cloud import storage

GCS_BUCKET = "job-crawling-data"
DATA_ROOT  = "/opt/airflow/data"


def upload_to_gcs(ds: str):
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)

    for site in ["wanted", "jobplanet"]:
        local_path = f"{DATA_ROOT}/{site}.result.jsonl"
        if os.path.exists(local_path):
            gcs_path = f"{ds}/{site}.result.jsonl"
            bucket.blob(gcs_path).upload_from_filename(local_path)
