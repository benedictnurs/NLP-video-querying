FROM apache/airflow:2.10.5-python3.12

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg openjdk-17-jre-headless curl fonts-dejavu-core \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf "$(dirname "$(dirname "$(readlink -f "$(which java)")")")" /usr/lib/jvm/java-17

ENV JAVA_HOME=/usr/lib/jvm/java-17
ENV SPARK_HOME=/opt/spark
ENV PATH="${JAVA_HOME}/bin:${SPARK_HOME}/bin:${PATH}"
ENV SPARK_NO_DAEMONIZE=true

RUN curl -fsSL https://archive.apache.org/dist/spark/spark-3.5.3/spark-3.5.3-bin-hadoop3.tgz \
    | tar -xz -C /opt \
    && mv /opt/spark-3.5.3-bin-hadoop3 /opt/spark

USER airflow
RUN pip install --no-cache-dir neo4j faster-whisper pyyaml pillow onnxruntime "protobuf<6" pyspark==3.5.3
