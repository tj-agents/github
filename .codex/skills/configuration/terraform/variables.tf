variable "declaration" {
  description = "The owner declaration, decoded from its github-configuration JSON."
  type        = any
}

variable "secret_values" {
  description = "Secret values by source name, supplied by the HCP workspace; declarations name sources only."
  type        = map(string)
  sensitive   = true
  default     = {}
}
