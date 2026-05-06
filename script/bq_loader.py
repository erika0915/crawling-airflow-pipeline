from google.cloud import bigquery

GCS_BUCKET = "job-crawling-data"
BQ_PROJECT = "kkium-prod"
BQ_DATASET = "job_postings"
BQ_TABLE   = "jobs"

SCHEMA = [
    bigquery.SchemaField("url",             "STRING"),
    bigquery.SchemaField("job_category",    "STRING"),
    bigquery.SchemaField("site",            "STRING"),
    bigquery.SchemaField("title",           "STRING"),
    bigquery.SchemaField("company_name",    "STRING"),
    bigquery.SchemaField("location",        "STRING"),
    bigquery.SchemaField("experience",      "STRING"),
    bigquery.SchemaField("deadline",        "STRING"),
    bigquery.SchemaField("employee_count",  "STRING"),
    bigquery.SchemaField("employment_type", "STRING"),
    bigquery.SchemaField("description",     "STRING"),
]


def load_to_bigquery(ds: str):
    client = bigquery.Client(project=BQ_PROJECT)

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    table_ref = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

    for site in ["wanted", "jobplanet"]:
        uri = f"gs://{GCS_BUCKET}/{ds}/{site}.result.jsonl"
        client.load_table_from_uri(uri, table_ref, job_config=job_config).result()
