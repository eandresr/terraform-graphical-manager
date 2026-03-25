output "service_port_map" {
  description = "Map of service names to their assigned ports."
  value       = local.service_port_map
}

output "all_hostnames" {
  description = "Flat list of all (env × service) hostnames."
  value       = local.all_hostnames
}

output "final_settings" {
  description = "Merged settings (defaults + custom overrides)."
  value       = local.final_settings
}

output "report_file" {
  description = "Path to the computed report JSON."
  value       = local_file.report.filename
}
