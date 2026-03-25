output "run_id" {
  description = "Short random ID for this Terraform run."
  value       = random_id.run_id.hex
}

output "trigger_value" {
  description = "Current value stored in the terraform_data trigger."
  value       = terraform_data.trigger_demo.output
}
