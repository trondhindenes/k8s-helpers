# k8s-utils

A collection of Kubernetes command-line utilities built with Python and Typer.

## Installation

This project uses [uv](https://github.com/astral-sh/uv) for package management. Make sure you have uv and uvx installed.

Just testing it out:
```shell
# Install dependencies
uvx k8s-helpers --help

```

Installation
```shell
uv tool install k8s-helpers
```
This installs the `k8s-helpers` command and the shorthand `k8sh`

## Commands

### podcount

Count non-daemonset pods per node in the current Kubernetes context.

```shell
# Use current context
k8sh podcount --help

# Specify a different context
k8sh podcount --context my-context

# Show nodegroup and zone labels, and sort by nodegroup
k8sh podcount \
--node-labels "eks.amazonaws.com/nodegroup" \
--node-labels "topology.kubernetes.io/zone" \
--sort "eks.amazonaws.com/nodegroup" \
--show-taints
```

This command:
- Retrieves all pods and nodes in the cluster
- Excludes pods managed by DaemonSets
- Displays a table showing pod count per node
- Shows total non-DaemonSet pod count

### delpods

Delete pods matching certain statuses across namespaces.

```shell
# Delete pods with specific statuses across all namespaces
k8sh delpods --all-namespaces --status ImagePullBackOff --status CrashLoopBackOff

# Preview what would be deleted (dry run)
k8sh delpods -A --status CrashLoopBackOff --dry-run

# Delete in specific namespace without confirmation
k8sh delpods -n default --status Error --yes

# Use specific context
k8sh delpods -c my-cluster -A --status ImagePullBackOff
```

Available arguments:
- `--all-namespaces`, `-A`: Search pods in all namespaces
- `--namespace`, `-n`: Specify a namespace (default: current context namespace)
- `--status`: Pod status to match (can be specified multiple times)
- `--dry-run`: Show what would be deleted without deleting
- `--yes`, `-y`: Skip confirmation prompt
- `--context`, `-c`: Kubernetes context to use
