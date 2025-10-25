"""Podcount command - Count non-daemonset pods per node"""

from collections import defaultdict
from typing import Optional

import typer
from kubernetes import client, config
from rich.console import Console
from rich.table import Table

console = Console()

def podcount_wrapper(
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubernetes context to use"
    ),
    node_labels: Optional[list[str]] = typer.Option(default=[], help="List of node labels to include"),
    sort: Optional[str] = typer.Option(
        "Node", help="Columnt to sort by"
    ),
):
    return podcount(context, node_labels, sort)

def podcount(
    context: str | None,
    node_labels: list[str] | None,
    sort: str = "Node",
):
    """
    Count non-daemonset pods per node in the current Kubernetes context.

    This command retrieves all pods and nodes in the cluster, excludes
    pods that are managed by DaemonSets, and displays the count of pods
    running on each node.
    """
    try:
        # Load kubernetes config
        if context:
            config.load_kube_config(context=context)
        else:
            config.load_kube_config()

        v1 = client.CoreV1Api()

        nodes = v1.list_node(watch=False)

        pods = v1.list_pod_for_all_namespaces(watch=False)



        # Create a set of node names to ensure we count all nodes
        all_nodes = {node.metadata.name for node in nodes.items}

        # Count pods per node (excluding DaemonSet pods)
        pod_count_per_node = defaultdict(int)

        for pod in pods.items:
            # Skip pods that are not assigned to a node yet
            if not pod.spec.node_name:
                continue

            # Check if pod is owned by a DaemonSet
            is_daemonset_pod = False
            if pod.metadata.owner_references:
                for owner in pod.metadata.owner_references:
                    if owner.kind == "DaemonSet":
                        is_daemonset_pod = True
                        break

            # Only count non-DaemonSet pods
            if not is_daemonset_pod:
                pod_count_per_node[pod.spec.node_name] += 1

        # Ensure all nodes are in the count (even if they have 0 pods)
        for node in all_nodes:
            if node not in pod_count_per_node:
                pod_count_per_node[node] = 0

        # Create a rich table for output
        table = Table(title="Non-DaemonSet Pod Count Per Node")
        table.add_column("Node", style="cyan", no_wrap=True)

        for label in node_labels:
            table.add_column(label, style="magenta", justify="right")

        table.add_column("Pod Count", style="magenta", justify="right")

        table_data = []

        # Sort by node name
        for node_name in sorted(pod_count_per_node.keys()):
            count = pod_count_per_node[node_name]

            node_obj = next((node for node in nodes.items if node.metadata.name == node_name), None)
            node_data = {
                "Node": node_obj.metadata.name,
            }

            for label in node_labels:
                label_value = node_obj.metadata.labels.get(label, "")

                node_data[label] = label_value
            node_data["Pod Count"] = count

            table_data.append(node_data)

        sorted_table_data = sorted(table_data, key=lambda x: x[sort])
        for row in sorted_table_data:
            renderable = []
            for col in row.values():
                if isinstance(col, int):
                    col = str(col)
                renderable.append(col)
            table.add_row(*renderable)

        console.print()
        console.print(table)
        console.print()

        # Print total
        total_pods = sum(pod_count_per_node.values())
        console.print(f"[bold]Total non-DaemonSet pods: {total_pods}[/bold]")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise typer.Exit(code=1)
