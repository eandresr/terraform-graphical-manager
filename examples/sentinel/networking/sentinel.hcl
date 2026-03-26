policy "no-ssh-open-to-world" {
  enforcement_level = "hard-mandatory"
}

policy "no-rdp-open-to-world" {
  enforcement_level = "hard-mandatory"
}

policy "no-unrestricted-ingress" {
  enforcement_level = "soft-mandatory"
}

policy "no-public-ip-on-launch" {
  enforcement_level = "soft-mandatory"
}

policy "vpc-flow-logs-enabled" {
  enforcement_level = "soft-mandatory"
}
