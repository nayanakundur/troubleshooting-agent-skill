# Overrides tfvars/ha2.tfvars' "instances" list to add a 9th gateway VM.
# themis/vms/aws/tfvars/ha2.tfvars only defines 8 instances (no gateway) —
# this file is loaded after it on the tofu command line to replace the
# instances list with the full 9-host set.
instances = [
  { name = "platform01", instance_type = "t3.medium" },
  { name = "platform02", instance_type = "t3.medium" },
  { name = "redis01",    instance_type = "t3.medium" },
  { name = "redis02",    instance_type = "t3.medium" },
  { name = "redis03",    instance_type = "t3.medium" },
  { name = "mongo01",    instance_type = "t3.medium" },
  { name = "mongo02",    instance_type = "t3.medium" },
  { name = "mongo03",    instance_type = "t3.medium" },
  { name = "gateway",    instance_type = "t3.medium" }
]
