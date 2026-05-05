from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

DATA_ROOT = "/opt/airflow/data"
SCRIPT    = "/opt/airflow/script/crawling.py"
N         = 20

default_args = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
}

def bash(site, method, extra=""):
    return f"python3 {SCRIPT} -s {site} -m {method} -d {DATA_ROOT} -n {N} {extra}".strip()

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

    wanted_get_url >> wanted_get_content >> wanted_postprocess
    jobplanet_get_url >> jobplanet_get_content >> jobplanet_postprocess
