output "api_url" {
  description = "Local URL for the API service."
  value       = "http://localhost:${random_integer.api_port.result}"
}

output "frontend_url" {
  description = "Local URL for the frontend service."
  value       = "http://localhost:${random_integer.frontend_port.result}"
}

output "docker_compose_path" {
  description = "Path to the generated docker-compose.yml."
  value       = local_file.docker_compose.filename
}

output "index_path" {
  description = "Path to the human-readable INDEX.txt file."
  value       = local_file.index.filename
}

output "service_env_paths" {
  description = "Map of service name to its .env file path."
  value       = { for svc, f in local_file.service_env : svc => f.filename }
}
