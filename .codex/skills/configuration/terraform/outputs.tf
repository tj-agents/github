output "merge_policies" {
  description = "Each declared repository's effective merge policy, after the owner default."
  value       = local.merge_policies

  precondition {
    condition     = length(local.invalid_merge_policies) == 0
    error_message = "Invalid .github/merge-policy.json (the repository's or the owner default) for: ${join(", ", local.invalid_merge_policies)}. Set approval to human, automated or none, merge to agents or human and default_branch_push to none or ci; review_check names a check only with approval automated, and the only other field is required_checks."
  }
}
