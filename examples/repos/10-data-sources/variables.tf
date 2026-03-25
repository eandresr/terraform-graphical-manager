variable "items" {
  description = "List of items to write to the seed file and shuffle."
  type        = list(string)
  default     = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
}

variable "seed_version" {
  description = "Version tag for the seed — changing it triggers a re-shuffle."
  type        = string
  default     = "v1"
}

variable "output_dir" {
  description = "Directory where generated files will be written."
  type        = string
  default     = "/tmp/tf-data-sources"
}
