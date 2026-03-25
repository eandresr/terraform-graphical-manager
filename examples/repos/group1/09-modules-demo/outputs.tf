output "backend_files" {
  description = "Files created by the backend-service scaffold."
  value       = module.backend_service.files_created
}

output "frontend_files" {
  description = "Files created by the frontend-app scaffold."
  value       = module.frontend_app.files_created
}
