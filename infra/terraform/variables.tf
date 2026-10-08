variable "cluster_name" {
  description = "Name of the kind cluster (must differ from the hand-made sre-sentinel cluster)."
  type        = string
  default     = "sre-sentinel-tf"

  validation {
    condition     = var.cluster_name != "sre-sentinel"
    error_message = "Refusing to manage the hand-made cluster named sre-sentinel."
  }
}

variable "node_version" {
  description = "kindest/node tag, same as the hand-made cluster."
  type        = string
  default     = "v1.37.0"
}

variable "host_port" {
  description = "Host port mapped to the app NodePort 30080 (8088 is used by the other cluster)."
  type        = number
  default     = 8089
}

variable "argocd_version" {
  description = "Pinned ArgoCD version (without the leading v)."
  type        = string
  default     = "3.4.9"
}

variable "gitops_raw_base" {
  description = "Raw URL of the argocd/ folder in the public GitOps repo."
  type        = string
  default     = "https://raw.githubusercontent.com/islemazz/sre-sentinel-gitops/main/argocd"
}
