output "app_suffix" {
  description = "Short alphanumeric suffix to append to resource names."
  value       = random_string.app_suffix.result
}

output "session_key" {
  description = "64-character session / CSRF key."
  value       = random_string.session_key.result
  sensitive   = true
}

output "db_password" {
  description = "Generated database password."
  value       = random_password.db_password.result
  sensitive   = true
}

output "db_password_bcrypt" {
  description = "Bcrypt hash of the database password."
  value       = random_password.db_password.bcrypt_hash
  sensitive   = true
}

output "correlation_id_hex" {
  description = "Random correlation ID encoded as hexadecimal."
  value       = random_id.correlation.hex
}

output "correlation_id_b64" {
  description = "Random correlation ID encoded as URL-safe base64."
  value       = random_id.correlation.b64_url
}
