import pulumi
from pulumi_gcp import container, storage, serviceaccount
from pulumi_kubernetes import Provider as K8sProvider
from pulumi_kubernetes.core.v1 import Secret, Namespace
from pulumi_kubernetes.helm.v3 import Chart, ChartOpts, FetchOpts

project_id = pulumi.Config("gcp").require("project")
region = pulumi.Config("gcp").get("region") or "us-central1"
zone = pulumi.Config("gcp").get("zone") or "us-central1-c"
location = "us-central1"
loki_bucket = storage.Bucket(
    "loki-bucket-1",
    name="loki_bucket-1",
    location=location,
    uniform_bucket_level_access=False,
    force_destroy=True, 
    versioning={
        "enabled": True,
    },
)

mimir_bucket = storage.Bucket(
    "mimir-bucket-1",
    name="mimir_bucket-1",
    location=location,
    uniform_bucket_level_access=False,
    force_destroy=True,
    versioning={"enabled": True},
)

storage_sa = serviceaccount.Account(
    "loki-mimir-storage-sa",
    account_id="loki-mimir-sa",
    display_name="Service Account for Loki and Mimir GCS Access"
)

loki_bucket_iam = storage.BucketIAMMember(
    "loki-bucket-iam-binding",
    bucket=loki_bucket.name,
    role="roles/storage.objectAdmin",
    member=storage_sa.email.apply(lambda email: f"serviceAccount:{email}")
)

mimir_bucket_iam = storage.BucketIAMMember(
    "mimir-bucket-iam-binding",
    bucket=mimir_bucket.name,
    role="roles/storage.objectAdmin",
    member=storage_sa.email.apply(lambda email: f"serviceAccount:{email}")
)


cluster = container.Cluster(
    "gke-cluster",
    min_master_version="1.32.4-gke.1353003",
    name="lgtm-stack-cluster",
    location=zone,
    initial_node_count=1,
    remove_default_node_pool=True,
    networking_mode="VPC_NATIVE",
    ip_allocation_policy={},
    deletion_protection=False,
)

node_pool = container.NodePool(
    "default-node-pool",
    cluster=cluster.name,
    location=zone,
    node_count=3,
    node_config=container.NodePoolNodeConfigArgs(
        machine_type="e2-medium",
        oauth_scopes=[
            "https://www.googleapis.com/auth/cloud-platform"
        ],
    )
)


sa_key = serviceaccount.Key(
    "loki-mimir-sa-key",
    service_account_id=storage_sa.name
)

k8s_provider = K8sProvider(
    "gke-k8s",
    kubeconfig=pulumi.Output.all(
        cluster.endpoint,
        cluster.name,
        cluster.master_auth
    ).apply(lambda args: f"""
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {args[2]['cluster_ca_certificate']}
    server: https://{args[0]}
  name: {args[1]}
contexts:
- context:
    cluster: {args[1]}
    user: {args[1]}
  name: {args[1]}
current-context: {args[1]}
kind: Config
preferences: {{}}
users:
- name: {args[1]}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: gke-gcloud-auth-plugin
      interactiveMode: IfAvailable
      provideClusterInfo: true
"""),
    opts=pulumi.ResourceOptions(depends_on=[cluster])
)

gcp_key_secret = Secret(
    "gcp-sa-secret",
    metadata={
        "name": "gcp-sa-secret",
        "namespace": "default",
    },
    string_data={
        "gcp-sa.json": sa_key.private_key,
    },
    opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[k8s_provider])
)

argocd_ns = Namespace(
    "argocd-namespace",
    metadata={"name": "argocd"},
    opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[k8s_provider])
)

argo_chart = Chart(
    "argo-cd",
    ChartOpts(
        chart="argo-cd",
        version="3.35.4",
        namespace=argocd_ns.metadata["name"],
        fetch_opts=FetchOpts(
            repo="https://argoproj.github.io/argo-helm"
        ),
        values={
            "server": {
                "service": {
                    "type": "LoadBalancer"
                }
            }
        }
    ),
    opts=pulumi.ResourceOptions(provider=k8s_provider,  depends_on=[k8s_provider])
)

pulumi.export("gke_cluster_name", cluster.name)
pulumi.export("loki_bucket", loki_bucket.name)
pulumi.export("mimir_bucket", mimir_bucket.name)
pulumi.export("service_account_email", storage_sa.email)
pulumi.export("k8s_secret_name", gcp_key_secret.metadata["name"])
pulumi.export("argocd_namespace", argocd_ns.metadata["name"])