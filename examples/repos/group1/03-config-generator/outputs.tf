output "config_file_path" {
  description = "Path to the generated YAML config file."
  value       = local_file.app_config.filename
}

output "secrets_file_path" {
  description = "Path to the generated .env secrets file."
  value       = local_sensitive_file.secrets_env.filename
}

output "app_id" {
  description = "Generated short application identifier."
  value       = random_string.app_id.result
}

output "http_port" {
  description = "Randomly allocated HTTP port for this application instance."
  value       = random_integer.http_port.result
}

output "secret_key" {
  description = "Generated application secret key."
  value       = random_password.secret_key.result
  sensitive   = true
}
