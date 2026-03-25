variable "services" {
  description = "List of service names that will each receive a unique pet name."
  type        = list(string)
  default     = ["api", "worker", "scheduler", "frontend", "cache"]
}

variable "env_prefix" {
  description = "Optional prefix prepended to the environment pet name."
  type        = string
  default     = "env"
}

variable "pet_word_count" {
  description = "Number of words in the environment pet name."
  type        = number
  default     = 2
}

variable "separator" {
  description = "Separator character between pet name words."
  type        = string
  default     = "-"
}

variable "output_dir" {
  description = "Directory where the names registry JSON file will be written."
  type        = string
  default     = "/tmp/tf-pet-names"
}
