from datetime import datetime, timedelta
import sys
sys.path.insert(0, "/opt/airflow/script")

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

from gcs_uploader import upload_to_gcs
from bq_loader import load_to_bigquery

DATA_ROOT = "/opt/airflow/data"
SCRIPT    = "/opt/airflow/script/crawling.py"
N         = 20

default_args = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
}

def bash(site, method):
    return f"python3 {SCRIPT} -s {site} -m {method} -d {DATA_ROOT} -n {N}"

def run_upload_to_gcs(**context):
    upload_to_gcs(ds=context["ds"])

def run_load_to_bigquery(**context):
    load_to_bigquery(ds=context["ds"])


with DAG(
    dag_id="job_crawling_pipeline",
    default_args=default_args,
    description="Wanted + Jobplanet 채용공고 크롤링",
    schedule_interval="0 9 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["crawling"],
) as dag:

    # 원티드
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

    # 잡플래닛
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

    # GCS 업로드 & BigQuery 적재
    upload_gcs = PythonOperator(
        task_id="upload_to_gcs",
        python_callable=run_upload_to_gcs,
        execution_timeout=timedelta(minutes=10),
    )
    load_bq = PythonOperator(
        task_id="load_to_bigquery",
        python_callable=run_load_to_bigquery,
        execution_timeout=timedelta(minutes=10),
    )

    # 의존관계
    wanted_get_url    >> wanted_get_content    >> wanted_postprocess    >> upload_gcs
    jobplanet_get_url >> jobplanet_get_content >> jobplanet_postprocess >> upload_gcs
    upload_gcs >> load_bq
