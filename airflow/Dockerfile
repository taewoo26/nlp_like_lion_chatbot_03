# Airflow 공식 이미지(Python 3.13) + 프로젝트 의존성.
# 코드·모델·데이터·.env 는 이미지에 넣지 않고 compose.yaml 에서 마운트한다.
ARG AIRFLOW_VERSION=3.4.0
FROM apache/airflow:${AIRFLOW_VERSION}-python3.13
ARG AIRFLOW_VERSION

COPY requirements.txt /tmp/requirements.txt

# 1) torch 는 CPU 전용 빌드를 먼저 설치한다 (GPU 빌드는 수 GB). 별도 RUN 이라 캐시된다.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 2) 프로젝트 의존성. 줄바꿈(CRLF)을 정리하고, Airflow 와 충돌하는 requests 고정만 완화한다.
#    (Airflow 3.4.0 은 requests>=2.32 필요. 프로젝트 requirements.txt 는 2.31.0 고정이라 충돌)
#    apache-airflow 를 같이 적어 프로젝트 패키지 때문에 Airflow 버전이 바뀌지 않게 한다.
RUN tr -d '\r' < /tmp/requirements.txt \
      | sed -E 's/^requests==.*/requests>=2.32.0/' > /tmp/requirements.airflow.txt \
 && pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /tmp/requirements.airflow.txt
