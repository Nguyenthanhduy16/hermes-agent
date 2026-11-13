# Sandbox image for benchmark runs (InfiAgent-DABench and similar data-analysis suites).
#
# The Hermes desktop sandbox has no data-science libraries, and benchmark runs keep the
# sandbox offline (terminal.docker_network: false) so the agent can neither pip-install
# nor fetch published answers. Everything the tasks need is baked in here, pinned.
#
#   docker build -t hermes-bench-sandbox:2 deploy/bench-sandbox
#
# Changing this file changes the experiment: rebuild under a new tag and record it.
# Tags: 1 = first RUN layer only (DABench pilot, 2026-11-12); 2 = + DA-Code layer.
FROM nousresearch/hermes-sandbox@sha256:c7d8607d076f26a64e0bd6828f74e1764af43fe3281368188459bb693bfa7567

RUN python3 -m pip install --no-cache-dir \
        numpy==2.2.4 \
        pandas==2.2.3 \
        scipy==1.15.2 \
        scikit-learn==1.6.1 \
        statsmodels==0.14.4 \
        matplotlib==3.10.1 \
        seaborn==0.13.2 \
    && python3 -c "import numpy, pandas, scipy, sklearn, statsmodels, matplotlib, seaborn"

# DA-Code: the libraries of its agent image (da_agent/images/da_agent-image, minus the
# BigQuery/Snowflake clients) and those its evaluator imports (da_agent/evaluators/metrics).
# The evaluator runs in a throwaway offline container of this image.
RUN python3 -m pip install --no-cache-dir \
        xgboost==2.1.4 \
        plotly==6.0.1 \
        tabulate==0.9.0 \
        duckdb==1.2.1 \
        openpyxl==3.1.5 \
        PyYAML==6.0.2 \
        fuzzywuzzy==0.18.0 \
        nltk==3.9.1 \
        rouge==1.0.1 \
        pillow==11.1.0 \
        opencv-python-headless==4.11.0.86 \
        json5==0.10.0 \
        jsonlines==4.0.0 \
        tqdm==4.67.1 \
    && python3 -c "import xgboost, plotly, tabulate, duckdb, openpyxl, yaml, fuzzywuzzy, nltk, rouge, PIL, cv2, json5, jsonlines, tqdm"
