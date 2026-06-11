# Daedalus runtime manifest template.
# This standalone vllm-skill repo owns future vLLM runtime edits.
__AUTH_PROXY_CONFIGMAP__
__CACHE_PV_MANIFEST__
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: __CACHE_PVC__
  namespace: __PROJECT__
  labels:
    app: __APP__
    app.kubernetes.io/name: __APP__
    app.kubernetes.io/part-of: daedalus
    app.kubernetes.io/managed-by: daedalus-skill
    app.kubernetes.io/component: runtime
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: __CACHE_SIZE__
__CACHE_STORAGE_CLASS_LINE__
  volumeMode: Filesystem
__CACHE_VOLUME_NAME_LINE__
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: __APP__
  namespace: __PROJECT__
  labels:
    app: __APP__
    app.kubernetes.io/name: __APP__
    app.kubernetes.io/part-of: daedalus
    app.kubernetes.io/managed-by: daedalus-skill
    app.kubernetes.io/component: runtime
spec:
  progressDeadlineSeconds: 3600
  strategy:
    type: Recreate
  replicas: 1
  selector:
    matchLabels:
      app: __APP__
  template:
    metadata:
      labels:
        app: __APP__
        app.kubernetes.io/name: __APP__
        app.kubernetes.io/part-of: daedalus
        app.kubernetes.io/managed-by: daedalus-skill
        app.kubernetes.io/component: runtime
    spec:
__NODE_SELECTOR_BLOCK__
      containers:
        - name: vllm-openai
          image: __IMAGE__
          imagePullPolicy: IfNotPresent
__HF_ENV_FROM__
          env:
            - name: HOME
              value: /tmp
            - name: HF_HOME
              value: /models-cache
            - name: TRANSFORMERS_CACHE
              value: /models-cache
            - name: XDG_CACHE_HOME
              value: /models-cache
            - name: VLLM_NO_USAGE_STATS
              value: "1"
          command:
            - python3
            - -m
            - vllm.entrypoints.openai.api_server
          args:
            - --host
            - "__VLLM_HOST__"
            - --port
            - "8000"
            - --model
            - "__MODEL__"
            - --served-model-name
            - "__SERVED_MODEL_NAME__"
            - --max-model-len
            - "__MAX_MODEL_LEN__"
            - --gpu-memory-utilization
            - "__GPU_MEMORY_UTILIZATION__"
            - --tensor-parallel-size
            - "__TENSOR_PARALLEL_SIZE__"
            - --dtype
            - "__DTYPE__"
          ports:
            - containerPort: 8000
              name: http
          readinessProbe:
            exec:
              command:
                - python3
                - -c
                - import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()
            initialDelaySeconds: 60
            periodSeconds: 15
            failureThreshold: 40
          startupProbe:
            exec:
              command:
                - python3
                - -c
                - import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()
            initialDelaySeconds: __STARTUP_INITIAL_DELAY_SECONDS__
            periodSeconds: __STARTUP_PERIOD_SECONDS__
            failureThreshold: __STARTUP_FAILURE_THRESHOLD__
          livenessProbe:
            exec:
              command:
                - python3
                - -c
                - import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()
            initialDelaySeconds: 180
            periodSeconds: 30
            failureThreshold: 10
          volumeMounts:
            - name: model-cache
              mountPath: /models-cache
            - name: tmp
              mountPath: /tmp
          resources:
            requests:
              cpu: "__CPU_REQUEST__"
              memory: __MEMORY_REQUEST__
              nvidia.com/gpu: "__GPU_COUNT__"
            limits:
              cpu: "__CPU_LIMIT__"
              memory: __MEMORY_LIMIT__
              nvidia.com/gpu: "__GPU_COUNT__"
__AUTH_PROXY_CONTAINER__
      volumes:
        - name: model-cache
          persistentVolumeClaim:
            claimName: __CACHE_PVC__
        - name: tmp
          emptyDir: {}
__AUTH_PROXY_VOLUME__
---
apiVersion: v1
kind: Service
metadata:
  name: __APP__
  namespace: __PROJECT__
  labels:
    app: __APP__
    app.kubernetes.io/name: __APP__
    app.kubernetes.io/part-of: daedalus
    app.kubernetes.io/managed-by: daedalus-skill
    app.kubernetes.io/component: runtime
spec:
  selector:
    app: __APP__
  ports:
    - name: http
      port: 8000
      targetPort: __SERVICE_TARGET_PORT__
__ROUTE_MANIFEST__
