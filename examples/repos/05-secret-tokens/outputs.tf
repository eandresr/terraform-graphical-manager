output "token_file_paths" {
  description = "Map of token name to the absolute path of its token file."
  value       = { for name, f in local_sensitive_file.token_files : name => f.filename }
}

output "index_file" {
  description = "Path to the INDEX file listing all token paths."
  value       = local_file.token_index.filename
}
