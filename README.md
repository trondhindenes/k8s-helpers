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
