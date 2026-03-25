output "output_dir" {
  description = "Directory where all files were written."
  value       = local.out
}

output "files_created" {
  description = "List of absolute paths of every file created."
  value = [
    local_file.readme.filename,
    local_file.gitignore.filename,
    local_file.env_example.filename,
    local_file.makefile.filename,
  ]
}
