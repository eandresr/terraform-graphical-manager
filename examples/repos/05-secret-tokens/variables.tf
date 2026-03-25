variable "token_names" {
  description = "Names of the tokens to generate (one file per name)."
  type        = list(string)
  default     = ["api-gateway", "internal-service", "webhook-receiver", "admin-cli", "metrics-collector"]
}

variable "token_length" {
  description = "Length (in characters) of each generated token."
  type        = number
  default     = 48

  validation {
    condition     = var.token_length >= 32
    error_message = "Tokens must be at least 32 characters long."
  }
}

variable "output_dir" {
  description = "Directory where token files will be written."
  type        = string
  default     = "/tmp/tf-tokens"
}
