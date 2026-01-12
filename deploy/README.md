# Deploy

This folder contains deployment manifests for Kubernetes and Argo CD.

## Kubernetes

- `kubernetes/deployment.yaml` deploys the NeuroPLC spine with bridge, OPC UA, and metrics enabled.
- Update the image reference (`neuroplc/spine:latest`) to match your registry.
- TLS certs are expected in the `neuroplc-tls` secret and audit logs in the `neuroplc-audit-pvc` PVC.
- For a local production-like container run, see `docker/compose.prod.yml`.

## Argo CD

- `argocd/application.yaml` defines an Argo CD Application pointing at `deploy/kubernetes`.
- Replace the `repoURL` with your Git repository and adjust the namespace if needed.
