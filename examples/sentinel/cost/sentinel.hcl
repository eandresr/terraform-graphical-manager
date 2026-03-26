policy "allowed-instance-types" {
  enforcement_level = "soft-mandatory"
}

policy "no-expensive-instance-types" {
  enforcement_level = "hard-mandatory"
}

policy "rds-no-multi-az-in-dev" {
  enforcement_level = "advisory"
}

policy "ec2-ebs-optimized-required" {
  enforcement_level = "advisory"
}
