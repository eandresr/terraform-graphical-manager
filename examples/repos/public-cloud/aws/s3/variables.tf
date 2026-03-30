variable "region" {
  description = "AWS region where the bucket will be created."
  type        = string
  default     = "eu-west-1"
}

variable "bucket_name" {
  description = "Globally unique name for the S3 bucket."
  type        = string
}
