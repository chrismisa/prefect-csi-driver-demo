
# Prefect on AKS Secret Store CSI Driver - Deployment Guide

## Table of Contents
- [Phase 1: Workload Identity & RBAC]
- [Phase 2: Azure Key Vault Integration]

---

## Phase 1: Workload Identity & RBAC

### Step 1.1: Enable Workload Identity on AKS

```bash
az aks update \
  --resource-group $RG_NAME \
  --name $CLUSTER_NAME \
  --enable-oidc-issuer \
  --enable-workload-identity
```

### Step 1.2: Retrieve OIDC Issuer

```bash
export AKS_OIDC_ISSUER="$(az aks show \
  -n $CLUSTER_NAME \
  -g $RG_NAME \
  --query 'oidcIssuerProfile.issuerUrl' \
  -otsv)"

echo $AKS_OIDC_ISSUER
```

### Step 1.3: Enable Azure Key Vault Secrets Provider

```bash
az aks enable-addons \
  --addons azure-keyvault-secrets-provider \
  --name $CLUSTER_NAME \
  --resource-group $RG_NAME
```

### Step 1.4: Create Azure Managed Identity

```bash
az identity create \
  --name "prefect-workload-id" \
  --resource-group $RG_NAME \
  --location $(echo $LOCATION | tr '[:upper:]' '[:lower:]')
```

### Step 1.5: Retrieve Managed Identity Client ID

```bash
export CLIENT_ID="$(az identity show \
  --resource-group $RG_NAME \
  --name "prefect-workload-id" \
  --query 'clientId' \
  -otsv)"

echo $CLIENT_ID
```

### Step 1.6: Create Federated Credential

```bash
az identity federated-credential create \
  --name "prefect-workload-federation" \
  --identity-name "prefect-workload-id" \
  --resource-group $RG_NAME \
  --issuer "${AKS_OIDC_ISSUER}" \
  --subject "system:serviceaccount:${NAMESPACE}:prefect-worker-sa" \
  --audience "api://AzureADTokenExchange"
```

## Phase 2: Azure Key Vault Integration

### Step 2.1: Enable CSI Driver (Already Done in Step 4.3)

Verify the CSI driver addon is enabled:

```bash
az aks addon list -g $RG_NAME -n $CLUSTER_NAME
```

### Step 2.2: Create SecretProviderClass

Create or update `helm/secrets-store-csi.yaml`:

```yaml
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: prefect-kv-secrets
  namespace: prefect
spec:
  provider: azure
  parameters:
    usePodIdentity: "false"
    useWorkloadIdentity: "true"
    clientID: "{MI CLIENT ID}"
    keyvaultName: "prefect-kv"
    tenantId: "{AZURE TENANT ID}"
    cloudName: ""
    objects: |
      array:
        - |
          objectName: chris-secret
          objectType: secret
```

### Step 2.3: Apply SecretProviderClass

```bash
kubectl apply -f helm/secrets-store-csi.yaml
```

### Step 2.4: Verify Helm Values Configuration

The volume mounts should already be configured in your `helm/values.yaml` from Phase 3.1. Verify the configuration includes:

```yaml
worker:
  extraVolumes:
    - name: secrets-store-inline
      csi:
        driver: secrets-store.csi.k8s.io
        readOnly: true
        volumeAttributes:
          secretProviderClass: "prefect-kv-secrets"

  extraVolumeMounts:
    - name: secrets-store-inline
      mountPath: "/mnt/secrets-store"
      readOnly: true
```

### Step 2.5: Assign Managed Identity Permissions

Grant the managed identity Secret Reader role on the Key Vault:

```bash
export KV_ID=$(az keyvault show --name $KEYVAULT_NAME --query 'id' -otsv)
export MI_PRINCIPAL_ID=$(az identity show -g $RG_NAME -n prefect-workload-id --query 'principalId' -otsv)

az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee-object-id $MI_PRINCIPAL_ID \
  --scope $KV_ID
```

### Step 2.6: Apply Helm Upgrade

```bash
helm upgrade prefect-worker prefect/prefect-worker \
  --namespace=$NAMESPACE \
  -f helm/values.yaml
```


### Step 2.7 Prefect Worker Pool - Update Job Template

Make sure in the Prefect worker pool job template contain below section - either update from UI or update via Terraform. 

```

"job_manifest": {
  "kind": "Job",
  "spec": {
    "template": {
      "metadata": {
        "labels": {
          "azure.workload.identity/use": "true" 
        }
      },
      "spec": {
        "serviceAccountName": "{{ service_account_name }}",
        "volumes": [
          {
            "name": "secrets-store-inline",
            "csi": {
              "driver": "secrets-store.csi.k8s.io",
              "readOnly": true,
              "volumeAttributes": {
                "secretProviderClass": "prefect-kv-secrets"
              }
            }
          }
        ],
        "containers": [
          {
            "name": "prefect-job",
            "image": "{{ image }}",
            "volumeMounts": [
              {
                "name": "secrets-store-inline",
                "mountPath": "/mnt/secrets-store",
                "readOnly": true
              }
            ],
            "env": "{{ env }}",
            "args": "{{ command }}",
            "imagePullPolicy": "{{ image_pull_policy }}"
          }
        ],
        "restartPolicy": "Never"
      }
    }
  }
}

```

*** Note: we need to define the naming convention for the secretProviderClass to reflect the workspace/namespace differences


### Step 2.7 Prefect Worker Pool - Variable Setup


Job Variables, Service Account Name and Namespace, under Worker Pool need to be updated. Service Account Name is the workload identity name, e.g., prefect-worker-sa 



### Optional - Mount Secrets on Prefect Worker pod

The worker Helm chart controls both the long‑running worker pod. CSI volume is
mounted to worker once Update `helm/values.yaml` with your Prefect credentials and Workload Identity
configuration. Add the `extraVolumes`/`extraVolumeMounts` under `worker` and
Mirror them inside a `baseJobTemplate` to propagate into flow-run pods:

```yaml
serviceAccount:
  create: true
  name: "prefect-worker-sa"
  annotations:
    azure.workload.identity/client-id: "{MI Id}"

worker:
  podLabels:
    azure.workload.identity/use: "true"
  cloudApiConfig:
    accountId: {[Prefect Account ID}
    workspaceId: {Prefect Workspace ID}
  config:
    workPool: AKS-worker-pool

  # --- mount volumes into the worker pod itself ---
  extraVolumes:
    - name: secrets-store-inline
      csi:
        driver: secrets-store.csi.k8s.io
        readOnly: true
        volumeAttributes:
          secretProviderClass: "prefect-kv-secrets"
  extraVolumeMounts:
    - name: secrets-store-inline
      mountPath: "/mnt/secrets-store"
      readOnly: true
```

Then run:

```bash
helm upgrade prefect-worker prefect/prefect-worker \
  --namespace=$NAMESPACE \
  -f helm/values.yaml
```


