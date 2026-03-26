policy "s3-no-public-acl" {
  enforcement_level = "hard-mandatory"
}

policy "s3-encryption-enabled" {
  enforcement_level = "hard-mandatory"
}

policy "iam-no-wildcard-actions" {
  enforcement_level = "hard-mandatory"
}

policy "iam-no-admin-policy" {
  enforcement_level = "hard-mandatory"
}

policy "tls-minimum-version" {
  enforcement_level = "soft-mandatory"
}

policy "secrets-not-in-variables" {
  enforcement_level = "hard-mandatory"
}
