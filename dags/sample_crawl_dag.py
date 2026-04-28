from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator


def crawl_jobs():
    import requests
    from bs4 import BeautifulSoup

    # TODO: 실제 크롤링 대상 URL로 교체
    print("크롤링 시작")
    print("크롤링 완료")


def save_jobs(**context):
    jobs = context["ti"].xcom_pull(task_ids="crawl_task")
    print(f"저장할 데이터: {jobs}")


default_args = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="job_crawling_pipeline",
    default_args=default_args,
    description="공고 크롤링 파이프라인",
    schedule_interval="0 9 * * *",  # 매일 오전 9시
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["crawling"],
) as dag:

    crawl_task = PythonOperator(
        task_id="crawl_task",
        python_callable=crawl_jobs,
    )

    save_task = PythonOperator(
        task_id="save_task",
        python_callable=save_jobs,
    )

    crawl_task >> save_task
