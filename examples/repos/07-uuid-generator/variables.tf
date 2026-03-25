variable "id_names" {
  description = "Logical names for the UUIDs to generate."
  type        = list(string)
  default     = ["user-service", "order-service", "payment-service", "notification-service"]
}

variable "batch_count" {
  description = "Number of anonymous/batch UUIDs to generate in addition to named ones."
  type        = number
  default     = 5
}

variable "output_dir" {
  description = "Directory where the UUID manifest JSON will be written."
  type        = string
  default     = "/tmp/tf-uuids"
}
