output "seed_content" {
  description = "Raw JSON content of the seed file (as read by the data source)."
  value       = data.local_file.seed_data.content
}

output "shuffled_items" {
  description = "Shuffled version of the items list."
  value       = random_shuffle.order.result
}

output "processed_file" {
  description = "Path to the processed output file."
  value       = local_file.processed.filename
}
