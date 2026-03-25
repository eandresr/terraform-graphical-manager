output "env_files" {
  description = "Map of environment name to its generated .env file path."
  value       = { for env, f in local_file.env : env => f.filename }
}

output "summary_file" {
  description = "Path to the summary listing all generated files."
  value       = local_file.summary.filename
}
