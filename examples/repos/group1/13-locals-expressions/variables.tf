variable "tags" {
  description = "List of tags to transform with for expressions."
  type        = list(string)
  default     = ["env-production", "team-platform", "env-staging", "cost-center-42", "env-dev"]
}

variable "services" {
  description = "Service names used in setproduct and zipmap examples."
  type        = list(string)
  default     = ["api", "worker", "cache"]
}

variable "service_ports" {
  description = "Ports aligned to var.services (must have same length)."
  type        = list(number)
  default     = [8080, 8081, 6379]
}

variable "environments" {
  description = "Environment names used in setproduct example."
  type        = list(string)
  default     = ["dev", "staging", "prod"]
}

variable "custom_settings" {
  description = "Settings that override the built-in defaults (merge example)."
  type        = map(any)
  default = {
    log_level = "DEBUG"
    debug     = true
  }
}

variable "sentence" {
  description = "Sentence from which words are extracted using regexall."
  type        = string
  default     = "Terraform makes infrastructure as code easy and repeatable."
}

variable "output_dir" {
  description = "Directory where the report JSON will be written."
  type        = string
  default     = "/tmp/tf-locals"
}
