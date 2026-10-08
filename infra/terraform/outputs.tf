output "kubeconfig" {
  description = "Kubeconfig file of the Terraform-built cluster."
  value       = local.kubeconfig
}

output "app_url" {
  description = "Where the app answers once ArgoCD has synced it."
  value       = "http://127.0.0.1:${var.host_port}"
}
