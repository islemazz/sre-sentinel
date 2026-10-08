locals {
  kubeconfig = abspath("${path.module}/${var.cluster_name}.kubeconfig")
}

# 1. The cluster: Terraform runs the kind CLI (same shape as k8s/kind-cluster.yaml).
resource "terraform_data" "cluster" {
  input = {
    name       = var.cluster_name
    kubeconfig = local.kubeconfig
  }
  triggers_replace = [var.cluster_name, var.node_version, var.host_port]

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-NoProfile", "-Command"]
    command     = <<-EOT
      $cfg = @('kind: Cluster', 'apiVersion: kind.x-k8s.io/v1alpha4', 'nodes:', '- role: control-plane', '  extraPortMappings:', '  - containerPort: 30080', '    hostPort: ${var.host_port}', '    listenAddress: 127.0.0.1') -join [Char]10
      $cfg | kind create cluster --name ${var.cluster_name} --image kindest/node:${var.node_version} --kubeconfig '${local.kubeconfig}' --wait 300s --config -
      if ($LASTEXITCODE -ne 0) { exit 1 }
    EOT
  }

  provisioner "local-exec" {
    when        = destroy
    interpreter = ["PowerShell", "-NoProfile", "-Command"]
    command     = <<-EOT
      kind delete cluster --name ${self.input.name}
      Remove-Item -Force '${self.input.kubeconfig}' -ErrorAction SilentlyContinue
    EOT
  }
}

# 2. ArgoCD, pinned, installed with server-side apply.
resource "terraform_data" "argocd" {
  depends_on       = [terraform_data.cluster]
  triggers_replace = [var.argocd_version, terraform_data.cluster.id]

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-NoProfile", "-Command"]
    command     = <<-EOT
      $kc = '${local.kubeconfig}'
      kubectl --kubeconfig $kc create namespace argocd
      if ($LASTEXITCODE -ne 0) { exit 1 }
      kubectl --kubeconfig $kc apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/v${var.argocd_version}/manifests/install.yaml
      if ($LASTEXITCODE -ne 0) { exit 1 }
      kubectl --kubeconfig $kc wait --for=condition=established crd/applications.argoproj.io --timeout=180s
      if ($LASTEXITCODE -ne 0) { exit 1 }
      kubectl --kubeconfig $kc -n argocd rollout status deploy/argocd-server --timeout=900s
      if ($LASTEXITCODE -ne 0) { exit 1 }
      kubectl --kubeconfig $kc -n argocd rollout status deploy/argocd-repo-server --timeout=900s
      if ($LASTEXITCODE -ne 0) { exit 1 }
      kubectl --kubeconfig $kc -n argocd rollout status statefulset/argocd-application-controller --timeout=900s
      if ($LASTEXITCODE -ne 0) { exit 1 }
    EOT
  }
}

# 3. Hand the app over to ArgoCD (GitOps takes it from here).
resource "terraform_data" "sentinel_app" {
  depends_on       = [terraform_data.argocd]
  triggers_replace = [var.gitops_raw_base]

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-NoProfile", "-Command"]
    command     = <<-EOT
      $kc = '${local.kubeconfig}'
      kubectl --kubeconfig $kc apply -f ${var.gitops_raw_base}/sentinel-app.yaml
      if ($LASTEXITCODE -ne 0) { exit 1 }
    EOT
  }
}
