output "debug_config_path" {
  description = "Path to the debug config file, or null if debug is disabled."
  value       = var.enable_debug ? local_file.debug_config[0].filename : null
}

output "admin_env_path" {
  description = "Path to the admin .env file, or null if create_admin is false."
  value       = var.create_admin ? local_sensitive_file.admin_env[0].filename : null
}

output "env_file_paths" {
  description = "Map of enabled environments to their .env file paths."
  value       = { for env, f in local_file.env_files : env => f.filename }
}

output "summary_path" {
  description = "Path to the summary file."
  value       = local_file.summary.filename
}
