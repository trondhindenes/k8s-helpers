"""Resource analysis command - Show per-node resource consumption (excluding DaemonSets)"""

from collections import defaultdict
from typing import Optional

import typer
from kubernetes import client, config
from rich.console import Console
from rich.table import Table

console = Console()


def parse_cpu(cpu_str: str | None) -> float:
    """Parse CPU string to millicores (float)."""
    if not cpu_str:
        return 0.0
    cpu_str = str(cpu_str)
    if cpu_str.endswith("m"):
        return float(cpu_str[:-1])
    elif cpu_str.endswith("n"):
        return float(cpu_str[:-1]) / 1_000_000
    else:
        # Assume cores
        return float(cpu_str) * 1000


def parse_memory(mem_str: str | None) -> float:
    """Parse memory string to MiB (float)."""
    if not mem_str:
        return 0.0
    mem_str = str(mem_str)

    multipliers = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "K": 1 / 1024,
        "M": 1,
        "G": 1024,
        "T": 1024 * 1024,
        "k": 1 / 1024,
    }

    for suffix, mult in multipliers.items():
        if mem_str.endswith(suffix):
            return float(mem_str[: -len(suffix)]) * mult

    # Assume bytes
    try:
        return float(mem_str) / (1024 * 1024)
    except ValueError:
        return 0.0


def format_cpu(millicores: float) -> str:
    """Format millicores for display."""
    if millicores >= 1000:
        return f"{millicores / 1000:.1f}"
    return f"{millicores:.0f}m"


def format_memory(mib: float) -> str:
    """Format MiB for display."""
    if mib >= 1024:
        return f"{mib / 1024:.1f}Gi"
    return f"{mib:.0f}Mi"


def resource_analysis_wrapper(
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubernetes context to use"
    ),
    show_pods: bool = typer.Option(
        False, "--show-pods", "-p", help="Show individual pod breakdown per node"
    ),
    sort: str = typer.Option(
        "node", "--sort", "-s", help="Sort by: node, cpu-req, cpu-lim, mem-req, mem-lim"
    ),
):
    """Analyze resource consumption per node (excluding DaemonSet pods)."""
    return resource_analysis(context, show_pods, sort)


def resource_analysis(
    context: str | None,
    show_pods: bool = False,
    sort: str = "node",
):
    """
    Analyze resource consumption per node, excluding DaemonSet pods.

    Shows CPU and memory requests/limits aggregated per node to help
    understand why each node exists and what's consuming its capacity.
    """
    try:
        # Load kubernetes config
        if context:
            config.load_kube_config(context=context)
        else:
            config.load_kube_config()

        v1 = client.CoreV1Api()
        custom_api = client.CustomObjectsApi()

        # Get all nodes
        nodes = v1.list_node(watch=False)
        node_info = {}
        for node in nodes.items:
            allocatable = node.status.allocatable or {}
            node_info[node.metadata.name] = {
                "cpu_allocatable": parse_cpu(allocatable.get("cpu")),
                "mem_allocatable": parse_memory(allocatable.get("memory")),
                "is_spot": node.metadata.labels.get("eks.amazonaws.com/capacityType") == "SPOT",
            }

        # Try to get pod metrics for actual usage
        pod_metrics = {}
        try:
            metrics = custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="pods",
            )
            for item in metrics.get("items", []):
                key = f"{item['metadata']['namespace']}/{item['metadata']['name']}"
                containers = item.get("containers", [])
                cpu_usage = sum(parse_cpu(c.get("usage", {}).get("cpu")) for c in containers)
                mem_usage = sum(parse_memory(c.get("usage", {}).get("memory")) for c in containers)
                pod_metrics[key] = {"cpu": cpu_usage, "mem": mem_usage}
        except Exception:
            # Metrics API not available
            pass

        # Get all pods
        pods = v1.list_pod_for_all_namespaces(watch=False)

        # Aggregate resources per node
        node_resources = defaultdict(lambda: {
            "cpu_requests": 0.0,
            "cpu_limits": 0.0,
            "mem_requests": 0.0,
            "mem_limits": 0.0,
            "cpu_usage": 0.0,
            "mem_usage": 0.0,
            "pod_count": 0,
            "pods": [],
        })

        for pod in pods.items:
            # Skip pods not assigned to a node
            if not pod.spec.node_name:
                continue

            # Skip DaemonSet pods
            is_daemonset = False
            if pod.metadata.owner_references:
                for owner in pod.metadata.owner_references:
                    if owner.kind == "DaemonSet":
                        is_daemonset = True
                        break
            if is_daemonset:
                continue

            # Skip non-running pods
            if pod.status.phase not in ("Running", "Pending"):
                continue

            node_name = pod.spec.node_name
            pod_key = f"{pod.metadata.namespace}/{pod.metadata.name}"

            # Sum container resources
            pod_cpu_req = 0.0
            pod_cpu_lim = 0.0
            pod_mem_req = 0.0
            pod_mem_lim = 0.0

            for container in pod.spec.containers:
                resources = container.resources or client.V1ResourceRequirements()
                requests = resources.requests or {}
                limits = resources.limits or {}

                pod_cpu_req += parse_cpu(requests.get("cpu"))
                pod_cpu_lim += parse_cpu(limits.get("cpu"))
                pod_mem_req += parse_memory(requests.get("memory"))
                pod_mem_lim += parse_memory(limits.get("memory"))

            # Add init containers (they run before main containers, so take max)
            # Actually for accounting purposes, we typically only count main containers
            # since init containers are transient

            node_resources[node_name]["cpu_requests"] += pod_cpu_req
            node_resources[node_name]["cpu_limits"] += pod_cpu_lim
            node_resources[node_name]["mem_requests"] += pod_mem_req
            node_resources[node_name]["mem_limits"] += pod_mem_lim
            node_resources[node_name]["pod_count"] += 1

            # Add actual usage if available
            if pod_key in pod_metrics:
                node_resources[node_name]["cpu_usage"] += pod_metrics[pod_key]["cpu"]
                node_resources[node_name]["mem_usage"] += pod_metrics[pod_key]["mem"]

            # Store pod details for --show-pods
            node_resources[node_name]["pods"].append({
                "name": pod_key,
                "cpu_req": pod_cpu_req,
                "cpu_lim": pod_cpu_lim,
                "mem_req": pod_mem_req,
                "mem_lim": pod_mem_lim,
                "cpu_usage": pod_metrics.get(pod_key, {}).get("cpu", 0),
                "mem_usage": pod_metrics.get(pod_key, {}).get("mem", 0),
            })

        # Ensure all nodes appear
        for node_name in node_info:
            if node_name not in node_resources:
                node_resources[node_name]  # Creates default entry

        # Sort nodes
        sort_keys = {
            "node": lambda x: x[0],
            "cpu-req": lambda x: -x[1]["cpu_requests"],
            "cpu-lim": lambda x: -x[1]["cpu_limits"],
            "mem-req": lambda x: -x[1]["mem_requests"],
            "mem-lim": lambda x: -x[1]["mem_limits"],
        }
        sort_fn = sort_keys.get(sort.lower(), sort_keys["node"])
        sorted_nodes = sorted(node_resources.items(), key=sort_fn)

        # Check if we have metrics data
        has_metrics = any(r["cpu_usage"] > 0 or r["mem_usage"] > 0 for r in node_resources.values())

        # Create summary table
        table = Table(title="Resource Analysis Per Node (excluding DaemonSets)")
        table.add_column("Node", style="cyan", no_wrap=True)
        table.add_column("Pods", justify="right")
        if has_metrics:
            table.add_column("CPU Usage", justify="right", style="yellow")
        table.add_column("CPU Req", justify="right", style="green")
        table.add_column("CPU Lim", justify="right", style="magenta")
        table.add_column("CPU Alloc", justify="right", style="dim")
        if has_metrics:
            table.add_column("Mem Usage", justify="right", style="yellow")
        table.add_column("Mem Req", justify="right", style="green")
        table.add_column("Mem Lim", justify="right", style="magenta")
        table.add_column("Mem Alloc", justify="right", style="dim")

        totals = {
            "pods": 0,
            "cpu_req": 0.0,
            "cpu_lim": 0.0,
            "cpu_alloc": 0.0,
            "cpu_usage": 0.0,
            "mem_req": 0.0,
            "mem_lim": 0.0,
            "mem_alloc": 0.0,
            "mem_usage": 0.0,
        }

        for node_name, resources in sorted_nodes:
            info = node_info.get(node_name, {"cpu_allocatable": 0, "mem_allocatable": 0, "is_spot": False})

            # Format node name with spot indicator
            if info["is_spot"]:
                node_display = f"[bold red]{node_name}[/bold red]"
            else:
                node_display = node_name

            row = [
                node_display,
                str(resources["pod_count"]),
            ]

            if has_metrics:
                row.append(format_cpu(resources["cpu_usage"]))

            row.extend([
                format_cpu(resources["cpu_requests"]),
                format_cpu(resources["cpu_limits"]),
                format_cpu(info["cpu_allocatable"]),
            ])

            if has_metrics:
                row.append(format_memory(resources["mem_usage"]))

            row.extend([
                format_memory(resources["mem_requests"]),
                format_memory(resources["mem_limits"]),
                format_memory(info["mem_allocatable"]),
            ])

            table.add_row(*row)

            # Accumulate totals
            totals["pods"] += resources["pod_count"]
            totals["cpu_req"] += resources["cpu_requests"]
            totals["cpu_lim"] += resources["cpu_limits"]
            totals["cpu_alloc"] += info["cpu_allocatable"]
            totals["cpu_usage"] += resources["cpu_usage"]
            totals["mem_req"] += resources["mem_requests"]
            totals["mem_lim"] += resources["mem_limits"]
            totals["mem_alloc"] += info["mem_allocatable"]
            totals["mem_usage"] += resources["mem_usage"]

        # Add totals row
        total_row = [
            "[bold]TOTAL[/bold]",
            f"[bold]{totals['pods']}[/bold]",
        ]
        if has_metrics:
            total_row.append(f"[bold]{format_cpu(totals['cpu_usage'])}[/bold]")
        total_row.extend([
            f"[bold]{format_cpu(totals['cpu_req'])}[/bold]",
            f"[bold]{format_cpu(totals['cpu_lim'])}[/bold]",
            f"[bold]{format_cpu(totals['cpu_alloc'])}[/bold]",
        ])
        if has_metrics:
            total_row.append(f"[bold]{format_memory(totals['mem_usage'])}[/bold]")
        total_row.extend([
            f"[bold]{format_memory(totals['mem_req'])}[/bold]",
            f"[bold]{format_memory(totals['mem_lim'])}[/bold]",
            f"[bold]{format_memory(totals['mem_alloc'])}[/bold]",
        ])
        table.add_row(*total_row)

        console.print()
        console.print(table)

        # Show per-pod breakdown if requested
        if show_pods:
            console.print()
            for node_name, resources in sorted_nodes:
                if not resources["pods"]:
                    continue

                info = node_info.get(node_name, {"is_spot": False})
                title_style = "bold red" if info["is_spot"] else "bold cyan"

                pod_table = Table(title=f"[{title_style}]{node_name}[/{title_style}] - Pod Details")
                pod_table.add_column("Pod", style="cyan", no_wrap=True)
                if has_metrics:
                    pod_table.add_column("CPU Usage", justify="right", style="yellow")
                pod_table.add_column("CPU Req", justify="right", style="green")
                pod_table.add_column("CPU Lim", justify="right", style="magenta")
                if has_metrics:
                    pod_table.add_column("Mem Usage", justify="right", style="yellow")
                pod_table.add_column("Mem Req", justify="right", style="green")
                pod_table.add_column("Mem Lim", justify="right", style="magenta")

                # Sort pods by memory request descending
                sorted_pods = sorted(resources["pods"], key=lambda p: -p["mem_req"])

                for pod in sorted_pods:
                    row = [pod["name"]]
                    if has_metrics:
                        row.append(format_cpu(pod["cpu_usage"]))
                    row.extend([
                        format_cpu(pod["cpu_req"]),
                        format_cpu(pod["cpu_lim"]),
                    ])
                    if has_metrics:
                        row.append(format_memory(pod["mem_usage"]))
                    row.extend([
                        format_memory(pod["mem_req"]),
                        format_memory(pod["mem_lim"]),
                    ])
                    pod_table.add_row(*row)

                console.print(pod_table)
                console.print()

        # Print summary
        console.print()
        if totals["cpu_alloc"] > 0:
            cpu_util = (totals["cpu_req"] / totals["cpu_alloc"]) * 100
            console.print(f"[bold]CPU Requests utilization:[/bold] {cpu_util:.1f}% of allocatable")
        if totals["mem_alloc"] > 0:
            mem_util = (totals["mem_req"] / totals["mem_alloc"]) * 100
            console.print(f"[bold]Memory Requests utilization:[/bold] {mem_util:.1f}% of allocatable")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise typer.Exit(code=1)
