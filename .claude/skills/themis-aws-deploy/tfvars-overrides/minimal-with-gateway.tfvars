# Overrides tfvars/minimal.tfvars' "instances" list to add a 4th gateway VM.
# themis/vms/aws/tfvars/minimal.tfvars only defines 3 instances (no gateway) —
# this file is loaded after it on the tofu command line to replace the
# instances list with the full 4-host set.
instances = [
  { name = "redis",    instance_type = "t3.medium" },
  { name = "mongo",    instance_type = "t3.medium" },
  { name = "platform", instance_type = "t3.large"  },
  { name = "gateway",  instance_type = "t3.medium" }
]
