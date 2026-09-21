# Overrides tfvars/asa.tfvars' "instances" list to add 2 gateway VMs (one per site).
# themis/vms/aws/tfvars/asa.tfvars only defines 16 instances (no gateway) —
# this file is loaded after it on the tofu command line to replace the
# instances list with the full 18-host set. Names/types copied verbatim from
# tfvars/asa.tfvars, with dc1-gateway/dc2-gateway appended.
instances = [
  # Data Center 1
  { name = "dc1-platform01", instance_type = "t3.medium" },
  { name = "dc1-platform02", instance_type = "t3.medium" },
  { name = "dc1-redis01",    instance_type = "t3.medium" },
  { name = "dc1-redis02",    instance_type = "t3.medium" },
  { name = "dc1-sentinel",   instance_type = "t3.medium" },
  { name = "dc1-mongo01",    instance_type = "t3.medium" },
  { name = "dc1-mongo02",    instance_type = "t3.medium" },
  { name = "dc1-gateway",    instance_type = "t3.medium" },
  # Data Center 2
  { name = "dc2-platform01", instance_type = "t3.medium" },
  { name = "dc2-platform02", instance_type = "t3.medium" },
  { name = "dc2-redis01",    instance_type = "t3.medium" },
  { name = "dc2-redis02",    instance_type = "t3.medium" },
  { name = "dc2-sentinel",   instance_type = "t3.medium" },
  { name = "dc2-mongo01",    instance_type = "t3.medium" },
  { name = "dc2-mongo02",    instance_type = "t3.medium" },
  { name = "dc2-gateway",    instance_type = "t3.medium" },
  # Data Center 3
  { name = "dc3-sentinel",   instance_type = "t3.medium" },
  { name = "dc3-arbiter",    instance_type = "t3.medium" }
]
