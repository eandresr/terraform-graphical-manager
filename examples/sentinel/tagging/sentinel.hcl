policy "required-tags" {
  enforcement_level = "hard-mandatory"
}

policy "no-default-tags-override" {
  enforcement_level = "advisory"
}

policy "environment-tag-valid-values" {
  enforcement_level = "soft-mandatory"
}
