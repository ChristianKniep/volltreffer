# wc2026 Helm chart

Deploys the World Cup 2026 team-tip prediction app (FastAPI) on Kubernetes.

The app keeps all state in a single **SQLite** file on a PersistentVolume, so it
runs as exactly **one replica**. The chart provisions:

- a `Deployment` (1 pod, `Recreate` strategy so it never double-mounts the volume)
- a `Service` (ClusterIP) and an `Ingress`
- a `PersistentVolumeClaim` for the database (`/data`)
- a `ConfigMap` (non-secret env) and a `Secret` (keys, passwords, tokens)
- a `ServiceAccount`

> Scaling to multiple app pods requires migrating the data layer to a shared
> database (e.g. Postgres). With SQLite, keep `replicaCount: 1`.

## 1. Build and push the image

The cluster pulls the image named by `image.repository:image.tag`. Build it from
the app directory (the `Dockerfile` is one level up from this chart) and push it
to a registry your cluster can reach.

```bash
# from wc2026-app/ (the directory containing the Dockerfile)
cd ..

# --- GitHub Container Registry example ---
docker build -t ghcr.io/<you>/wc2026:1.0.0 .
echo "$GHCR_TOKEN" | docker login ghcr.io -u <you> --password-stdin
docker push ghcr.io/<you>/wc2026:1.0.0

# --- Docker Hub example ---
# docker build -t <you>/wc2026:1.0.0 .
# docker login
# docker push <you>/wc2026:1.0.0

# --- Local kind / minikube (no registry needed) ---
# docker build -t wc2026:1.0.0 .
# kind load docker-image wc2026:1.0.0
# minikube image load wc2026:1.0.0
```

If the registry is private, create a pull secret and reference it:

```bash
kubectl create secret docker-registry regcred \
  --docker-server=ghcr.io --docker-username=<you> --docker-password=$GHCR_TOKEN \
  -n wc2026
# then set: --set imagePullSecrets[0].name=regcred
```

## 2. Install

```bash
helm install wc2026 ./helm/wc2026 \
  --namespace wc2026 --create-namespace \
  --set image.repository=ghcr.io/<you>/wc2026 \
  --set image.tag=1.0.0 \
  --set ingress.hosts[0].host=wc2026.example.com \
  --set secrets.adminPassword='<strong-password>' \
  --set secrets.appSecretKey="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
```

Or with a values file (recommended for anything non-trivial):

```bash
helm install wc2026 ./helm/wc2026 -n wc2026 --create-namespace -f my-values.yaml
```

```yaml
# my-values.yaml
image:
  repository: ghcr.io/<you>/wc2026
  tag: "1.0.0"
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: wc2026.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: wc2026-tls
      hosts:
        - wc2026.example.com
config:
  cookieSecure: "true"        # you are serving over HTTPS
  defaultTz: "Europe/Berlin"
secrets:
  adminPassword: "<strong-password>"
  appSecretKey: "<long-random-stable-value>"
  # automationToken / footballDataToken / teamtipToken as needed
persistence:
  size: 1Gi
  storageClass: ""            # "" = cluster default
```

## 3. Upgrade / uninstall

```bash
helm upgrade wc2026 ./helm/wc2026 -n wc2026 -f my-values.yaml
helm uninstall wc2026 -n wc2026   # PVC is retained (resource-policy: keep)
```

## Key values

| Key | Default | Notes |
|-----|---------|-------|
| `replicaCount` | `1` | Keep at 1 (SQLite single writer). |
| `image.repository` / `image.tag` | `ghcr.io/christiankniep/wc2026` / `""` | Your pushed image. Empty tag falls back to `appVersion`. |
| `ingress.enabled` / `ingress.hosts[].host` | `true` / `wc2026.example.com` | External hostname. |
| `ingress.className` | `""` | e.g. `nginx`, `traefik`. |
| `persistence.size` | `1Gi` | Database volume size. |
| `persistence.storageClass` | `""` | `""` = default; `"-"` = disable dynamic provisioning. |
| `config.allowRegistration` | `"true"` | `"false"` = invite-only. |
| `config.cookieSecure` | `"false"` | Set `"true"` behind HTTPS. |
| `config.defaultTz` | `Europe/Berlin` | Fallback timezone. |
| `secrets.appSecretKey` | `""` | Auto-generated & preserved if empty. **Keep stable.** |
| `secrets.adminPassword` | `change-me` | First-start admin password. |
| `secrets.existingSecret` | `""` | Use a pre-created Secret instead. |

### About `secrets.appSecretKey`

It encrypts stored betting credentials. If you leave it empty, the chart
generates one on first install and **re-reads the existing value on every
upgrade** so it stays stable. Changing it later makes already-saved credentials
unreadable. To manage it yourself, set a long random value (and treat it like a
password).

## Validate locally

```bash
helm lint ./helm/wc2026
helm template wc2026 ./helm/wc2026 -f my-values.yaml | kubectl apply --dry-run=client -f -
```
