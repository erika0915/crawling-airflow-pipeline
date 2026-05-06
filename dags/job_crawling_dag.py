from datetime import datetime, timedelta
import os
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

DATA_ROOT  = "/opt/airflow/data"
SCRIPT     = "/opt/airflow/script/crawling.py"
N          = 20
GCS_BUCKET = "job-crawling-data"
BQ_PROJECT = "kkium-prod"
BQ_DATASET = "job_postings"
BQ_TABLE   = "jobs"

default_args = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
}

def bash(site, method):
    return f"python3 {SCRIPT} -s {site} -m {method} -d {DATA_ROOT} -n {N}"


def upload_to_gcs(**context):
    from google.cloud import storage
    ds = context["ds"]
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)

    for site in ["wanted", "jobplanet"]:
        local_path = f"{DATA_ROOT}/{site}.result.jsonl"
        if os.path.exists(local_path):
            gcs_path = f"{ds}/{site}.result.jsonl"
            bucket.blob(gcs_path).upload_from_filename(local_path)


def load_to_bigquery(**context):
    from google.cloud import bigquery
    ds = context["ds"]
    client = bigquery.Client(project=BQ_PROJECT)

    schema = [
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

    job_config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    table_ref = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

    for site in ["wanted", "jobplanet"]:
        uri = f"gs://{GCS_BUCKET}/{ds}/{site}.result.jsonl"
        client.load_table_from_uri(uri, table_ref, job_config=job_config).result()


with DAG(
    dag_id="job_crawling_pipeline",
    default_args=default_args,
    description="Wanted + Jobplanet 채용공고 크롤링",
    schedule_interval="0 9 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["crawling"],
) as dag:

    # ── Wanted ──────────────────────────────────────────
    wanted_get_url = BashOperator(
        task_id="wanted.get_url_list",
        bash_command=bash("wanted", "get_url_list"),
        execution_timeout=timedelta(minutes=30),
    )
    wanted_get_content = BashOperator(
        task_id="wanted.get_recruit_content_info",
        bash_command=bash("wanted", "get_recruit_content_info"),
        execution_timeout=timedelta(hours=2),
    )
    wanted_postprocess = BashOperator(
        task_id="wanted.postprocess",
        bash_command=bash("wanted", "postprocess"),
        execution_timeout=timedelta(minutes=10),
    )

    # ── Jobplanet ────────────────────────────────────────
    jobplanet_get_url = BashOperator(
        task_id="jobplanet.get_url_list",
        bash_command=bash("jobplanet", "get_url_list"),
        execution_timeout=timedelta(minutes=30),
    )
    jobplanet_get_content = BashOperator(
        task_id="jobplanet.get_recruit_content_info",
        bash_command=bash("jobplanet", "get_recruit_content_info"),
        execution_timeout=timedelta(hours=2),
    )
    jobplanet_postprocess = BashOperator(
        task_id="jobplanet.postprocess",
        bash_command=bash("jobplanet", "postprocess"),
        execution_timeout=timedelta(minutes=10),
    )

    # ── GCS + BigQuery ───────────────────────────────────
    upload_gcs = PythonOperator(
        task_id="upload_to_gcs",
        python_callable=upload_to_gcs,
        execution_timeout=timedelta(minutes=10),
    )
    load_bq = PythonOperator(
        task_id="load_to_bigquery",
        python_callable=load_to_bigquery,
        execution_timeout=timedelta(minutes=10),
    )

    # ── 의존관계 ──────────────────────────────────────────
    wanted_get_url    >> wanted_get_content    >> wanted_postprocess    >> upload_gcs
    jobplanet_get_url >> jobplanet_get_content >> jobplanet_postprocess >> upload_gcs
    upload_gcs >> load_bq
