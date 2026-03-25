output "uuid_map" {
  description = "Named UUIDs as a key-value map."
  value       = { for name, r in random_uuid.ids : name => r.result }
}

output "uuid_batch" {
  description = "List of batch UUIDs."
  value       = [for r in random_uuid.batch : r.result]
}

output "manifest_file" {
  description = "Path to the JSON manifest containing all generated UUIDs."
  value       = local_file.uuid_manifest.filename
}
