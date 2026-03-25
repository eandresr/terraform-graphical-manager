output "environment_name" {
  description = "Human-readable name generated for this environment."
  value       = random_pet.environment.id
}

output "service_names" {
  description = "Map of each service to its generated pet name."
  value       = { for svc, r in random_pet.services : svc => r.id }
}

output "registry_file" {
  description = "Path to the JSON file containing all generated names."
  value       = local_file.names_registry.filename
}
