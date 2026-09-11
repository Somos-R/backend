# Kubernetes — Implementación y Guía de Pruebas

## Arquitectura desplegada

```
Internet
    │
    ▼
NGINX Ingress (somosr.com)
    ├── /api/* ──────────────► backend-service:8000
    │                              │
    │                         somos-r-backend (Deployment)
    │                         2–10 réplicas (HPA)
    │                              │
    │                     init containers:
    │                       1. wait-for-postgres
    │                       2. alembic upgrade head
    │                              │
    └── /* ──────────────────► frontend-service:80
                                   │
                              somos-r-frontend (Deployment)
                              2 réplicas

PostgreSQL (StatefulSet, 1 réplica, PVC 10Gi)
    imagen: postgis/postgis:15-3.4
```

---

## Archivos creados

### Estructura `backend/k8s/`

```
k8s/
├── namespace.yaml          # Namespace somos-r-prod
├── configmap.yaml          # Variables no sensibles (ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, etc.)
├── secret.yaml             # Variables sensibles (NO commitear — en .gitignore)
├── ingress.yaml            # NGINX Ingress con TLS (cert-manager + Let's Encrypt)
├── postgres/
│   ├── statefulset.yaml    # PostgreSQL + PostGIS con PVC 10Gi
│   └── service.yaml        # ClusterIP + ConfigMap init scripts PostGIS
├── backend/
│   ├── deployment.yaml     # 2 réplicas, RollingUpdate, init containers
│   ├── service.yaml        # ClusterIP puerto 8000
│   └── hpa.yaml            # Autoescalado CPU 50% / Memoria 70%, máx 10
└── frontend/
    ├── deployment.yaml     # 2 réplicas, RollingUpdate
    └── service.yaml        # ClusterIP puerto 80
```

### Dockerfiles

| Archivo | Ubicación | Propósito |
|---|---|---|
| `Dockerfile.prod` | `backend/` | Multi-stage: Poetry 2.x → imagen slim Python 3.11 |
| `Dockerfile` | `somos-r-web/` | Multi-stage: pnpm build → nginx:alpine |

### CI/CD

| Archivo | Repositorio | Triggers |
|---|---|---|
| `.github/workflows/ci-cd.yml` | backend | push a `main`: test → build → deploy |
| `.github/workflows/ci-cd.yml` | somos-r-web | push a `main`: build → deploy |

---

## Decisiones de diseño

### Init containers en el backend
```yaml
initContainers:
  - name: wait-for-postgres    # busybox: nc -z postgres-service 5432
  - name: run-migrations       # misma imagen del backend: alembic upgrade head
```
Esto garantiza que las migraciones de DB **siempre se ejecutan antes** de que el backend reciba tráfico, y que nunca arrancan si PostgreSQL no está listo.

### HPA (Horizontal Pod Autoscaler)
```
minReplicas: 2   maxReplicas: 10
CPU trigger:    50% de uso promedio
Memory trigger: 70% de uso promedio
```
Con 2 réplicas mínimas se garantiza disponibilidad ante falla de un nodo.

### RollingUpdate con `maxUnavailable: 0`
```yaml
strategy:
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
```
Despliega el pod nuevo antes de bajar el viejo → zero-downtime deployments.

### Dockerfile.prod — Poetry 2.x
El `pyproject.toml` usa formato PEP 621 (`[project]`) que requiere Poetry 2.x. Se corrigió de `poetry==1.8.3` (Poetry 1.x, incompatible) a `"poetry>=2.0.0,<3.0.0"`.

---

## Pruebas locales con Minikube

### 1. Prerequisitos

```bash
# Instalar Minikube
# Windows: winget install Kubernetes.minikube
# Mac: brew install minikube

# Instalar kubectl
# Windows: winget install Kubernetes.kubectl
# Mac: brew install kubectl

# Iniciar cluster
minikube start --driver=docker --cpus=4 --memory=4096

# Habilitar addons necesarios
minikube addons enable ingress
minikube addons enable metrics-server   # necesario para HPA
```

### 2. Construir imágenes dentro de Minikube

En lugar de publicar a ghcr.io, puedes construir directamente en el registry de Minikube:

```bash
# Apunta Docker al daemon de Minikube
eval $(minikube docker-env)          # Linux/Mac
# En Windows PowerShell:
# minikube docker-env | Invoke-Expression

# Build del backend
cd backend
docker build -f Dockerfile.prod -t ghcr.io/somos-r/backend:latest .

# Build del frontend
cd ../somos-r-web
docker build -t ghcr.io/somos-r/frontend:latest .
```

> Importante: las imágenes construidas así solo existen en Minikube. Agrega `imagePullPolicy: Never` en los deployments para evitar que intente bajarlas de internet.

### 3. Crear el Secret

El archivo `k8s/secret.yaml` **no está en git** (ignorado por seguridad). Debes crearlo manualmente:

```bash
# Opción A: desde el archivo template (editar primero con valores reales)
cp backend/k8s/secret.yaml.example backend/k8s/secret.yaml
# Editar backend/k8s/secret.yaml con los valores reales en base64

# Opción B: crear directamente con kubectl
kubectl create namespace somos-r-prod --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic somos-r-secret \
  --namespace somos-r-prod \
  --from-literal=SECRET_KEY="tu-clave-secreta-jwt" \
  --from-literal=DATABASE_URL="postgresql+psycopg2://postgres:password@postgres-service:5432/somos_r_prod" \
  --from-literal=DATABASE_PASSWORD="tu-password-postgres"
```

### 4. Aplicar manifiestos en orden

```bash
cd backend

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
# Secret ya creado en paso anterior, o:
kubectl apply -f k8s/secret.yaml

kubectl apply -f k8s/postgres/
kubectl apply -f k8s/backend/
kubectl apply -f k8s/frontend/
kubectl apply -f k8s/ingress.yaml
```

### 5. Verificar que todo está corriendo

```bash
# Ver todos los pods del namespace
kubectl get pods -n somos-r-prod

# Salida esperada:
# NAME                                READY   STATUS    RESTARTS   AGE
# postgres-0                          1/1     Running   0          2m
# somos-r-backend-xxx-yyy             1/1     Running   0          90s
# somos-r-backend-xxx-zzz             1/1     Running   0          90s
# somos-r-frontend-xxx-yyy            1/1     Running   0          85s
# somos-r-frontend-xxx-zzz            1/1     Running   0          85s

# Ver deployments
kubectl get deployments -n somos-r-prod

# Ver servicios
kubectl get services -n somos-r-prod

# Ver ingress
kubectl get ingress -n somos-r-prod
```

### 6. Ver logs de un pod

```bash
# Ver logs del backend (reemplaza el nombre del pod)
kubectl logs -n somos-r-prod somos-r-backend-xxx-yyy

# Ver logs en tiempo real
kubectl logs -n somos-r-prod somos-r-backend-xxx-yyy -f

# Ver logs de init containers (útil para debug de migraciones)
kubectl logs -n somos-r-prod somos-r-backend-xxx-yyy -c wait-for-postgres
kubectl logs -n somos-r-prod somos-r-backend-xxx-yyy -c run-migrations

# Ver logs del frontend (nginx)
kubectl logs -n somos-r-prod somos-r-frontend-xxx-yyy
```

### 7. Probar el acceso

```bash
# Obtener la IP de Minikube
minikube ip
# Ej: 192.168.49.2

# Agregar al /etc/hosts (o C:\Windows\System32\drivers\etc\hosts en Windows)
# 192.168.49.2  somosr.com

# O usar port-forward para prueba rápida sin DNS:
kubectl port-forward -n somos-r-prod service/backend-service 8000:8000
# Ahora http://localhost:8000/docs muestra la API

kubectl port-forward -n somos-r-prod service/frontend-service 3000:80
# Ahora http://localhost:3000 muestra el frontend
```

### 8. Verificar el Ingress en acción

```bash
# Con Minikube tunnel (abre una ventana separada)
minikube tunnel

# En otra terminal:
curl -H "Host: somosr.com" http://$(minikube ip)/api/health
# Debe responder: {"status": "ok"}

curl -H "Host: somosr.com" http://$(minikube ip)/
# Debe responder: HTML del frontend
```

### 9. Probar el HPA (autoescalado)

```bash
# Ver estado actual del HPA
kubectl get hpa -n somos-r-prod
# NAME          REFERENCE                    TARGETS          MINPODS   MAXPODS   REPLICAS
# backend-hpa   Deployment/somos-r-backend   10%/50%, 5%/70%   2         10        2

# Generar carga para ver el escalado
kubectl run load-test --image=busybox:1.35 -it --rm -- sh
# Dentro del pod:
while true; do wget -q -O- http://backend-service.somos-r-prod:8000/health; done

# En otra terminal, observar cómo aumentan las réplicas:
kubectl get hpa -n somos-r-prod -w
```

### 10. Probar un rolling update (deployment sin downtime)

```bash
# Simular un nuevo deploy cambiando la imagen
kubectl set image deployment/somos-r-backend \
  backend=ghcr.io/somos-r/backend:nueva-version \
  -n somos-r-prod

# Observar el rollout
kubectl rollout status deployment/somos-r-backend -n somos-r-prod

# Si algo falla, hacer rollback
kubectl rollout undo deployment/somos-r-backend -n somos-r-prod
```

### 11. Inspeccionar el StatefulSet de PostgreSQL

```bash
# Ver el StatefulSet
kubectl get statefulsets -n somos-r-prod

# Conectarse directamente a la DB
kubectl exec -it -n somos-r-prod postgres-0 -- psql -U postgres -d somos_r_prod

# Dentro de psql:
\dt          -- listar tablas
SELECT count(*) FROM users;
SELECT count(*) FROM weighings;
\q
```

### 12. Ver el ConfigMap y Secret aplicados

```bash
# Ver ConfigMap (valores en texto plano)
kubectl get configmap somos-r-config -n somos-r-prod -o yaml

# Ver Secret (valores en base64, no en texto plano)
kubectl get secret somos-r-secret -n somos-r-prod -o yaml

# Decodificar un valor específico del secret
kubectl get secret somos-r-secret -n somos-r-prod \
  -o jsonpath='{.data.DATABASE_PASSWORD}' | base64 -d
```

---

## CI/CD — Cómo funciona el pipeline

### Backend (`.github/workflows/ci-cd.yml`)

```
Push a main
    │
    ├─ Job: test
    │   └── poetry install → pytest (continue-on-error: true)
    │
    ├─ Job: build (necesita test)
    │   ├── docker/login-action → ghcr.io
    │   ├── docker/metadata-action → tags: sha-corto + latest
    │   └── docker/build-push-action → Dockerfile.prod
    │
    └─ Job: deploy (necesita build, solo en main)
        ├── azure/setup-kubectl
        ├── Decodifica KUBECONFIG desde secret
        ├── kubectl apply -f k8s/  (aplica todos los manifiestos)
        ├── kubectl set image → nueva imagen con sha del commit
        └── kubectl rollout status → espera 120s
```

### Frontend (`.github/workflows/ci-cd.yml`)

```
Push a main
    │
    └─ Job: build + deploy
        ├── docker/build-push-action → Dockerfile (con ARG VITE_API_URL)
        └── kubectl set image deployment/somos-r-frontend
```

### Secrets requeridos en GitHub

Ir a **Settings → Secrets and variables → Actions** del repositorio y crear:

| Secret | Valor |
|---|---|
| `KUBECONFIG` | Contenido del kubeconfig en **base64** (`base64 ~/.kube/config`) |

### Variables requeridas en GitHub

Ir a **Settings → Secrets and variables → Actions → Variables**:

| Variable | Valor |
|---|---|
| `VITE_API_URL` | `https://somosr.com/api` |

---

## Comandos de diagnóstico rápido

```bash
# ¿Están corriendo los pods?
kubectl get pods -n somos-r-prod

# ¿Por qué un pod no arranca?
kubectl describe pod <nombre-pod> -n somos-r-prod

# ¿Qué eventos hay en el namespace?
kubectl get events -n somos-r-prod --sort-by='.lastTimestamp'

# ¿Cuántos recursos consumen los pods?
kubectl top pods -n somos-r-prod

# ¿Está el HPA recibiendo métricas?
kubectl describe hpa backend-hpa -n somos-r-prod

# Borrar todo y empezar de cero (¡destructivo!)
kubectl delete namespace somos-r-prod
```

---

## Evidencia de que Kubernetes está activo

Cuando el sistema está corriendo en Kubernetes, estas son las señales que lo confirman:

1. **Múltiples réplicas:** `kubectl get pods -n somos-r-prod` muestra 2+ pods del backend y 2+ del frontend.

2. **Init containers ejecutados:** Los logs de `run-migrations` muestran `INFO  [alembic.runtime.migration] Running upgrade` — confirmando que Alembic corrió dentro del cluster.

3. **Health probes activas:** `kubectl describe pod <backend-pod> -n somos-r-prod` muestra en la sección `Conditions` que `Liveness` y `Readiness` están en `True`.

4. **HPA midiendo métricas:** `kubectl get hpa -n somos-r-prod` muestra valores reales de CPU/Memoria en la columna `TARGETS` (no `<unknown>`).

5. **Ingress con IP asignada:** `kubectl get ingress -n somos-r-prod` muestra una IP en la columna `ADDRESS`.

6. **PVC de PostgreSQL bound:** `kubectl get pvc -n somos-r-prod` muestra `STATUS = Bound` y `CAPACITY = 10Gi`.
