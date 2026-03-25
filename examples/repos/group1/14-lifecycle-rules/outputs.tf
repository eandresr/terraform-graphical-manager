output "server_name" {
  description = "Generated pet name for the server (recreated with create_before_destroy)."
  value       = random_pet.server_name.id
}

output "immutable_config_path" {
  description = "Path to the prevent_destroy–protected config file."
  value       = local_file.immutable_config.filename
}

output "runtime_state_path" {
  description = "Path to the file whose content changes are ignored."
  value       = local_file.ignored_content.filename
}

output "versioned_config_path" {
  description = "Path to the file replaced when app_version changes."
  value       = local_file.versioned_config.filename
}
