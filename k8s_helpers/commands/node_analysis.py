"""Node analysis command - Analyze resource waste for pods on a specific node"""

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


def waste_color(waste_pct: float) -> str:
    """Return color based on waste percentage."""
    if waste_pct >= 80:
        return "bold red"
    elif waste_pct >= 60:
        return "red"
    elif waste_pct >= 40:
        return "yellow"
    elif waste_pct >= 20:
        return "dim"
    else:
        return "green"


def format_waste(waste_pct: float) -> str:
    """Format waste percentage with color."""
    color = waste_color(waste_pct)
    return f"[{color}]{waste_pct:.0f}%[/{color}]"


def node_analysis_wrapper(
    node: str = typer.Argument(..., help="Node name to analyze"),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubernetes context to use"
    ),
    sort: str = typer.Option(
        "mem-waste", "--sort", "-s",
        help="Sort by: name, cpu-waste, mem-waste, cpu-req, mem-req"
    ),
):
    """Analyze resource waste for pods on a specific node."""
    return node_analysis(node, context, sort)


def node_analysis(
    node: str,
    context: str | None,
    sort: str = "mem-waste",
):
    """
    Analyze resource waste for pods on a specific node.

    Shows actual usage vs requests for each pod to identify over-provisioned
    workloads. Waste is calculated as (requests - usage) / requests.
    """
    try:
        # Load kubernetes config
        if context:
            config.load_kube_config(context=context)
        else:
            config.load_kube_config()

        v1 = client.CoreV1Api()
        custom_api = client.CustomObjectsApi()

        # Verify node exists
        try:
            node_obj = v1.read_node(name=node)
        except client.exceptions.ApiException as e:
            if e.status == 404:
                console.print(f"[red]Error: Node '{node}' not found[/red]")
                raise typer.Exit(code=1)
            raise

        # Get node info
        allocatable = node_obj.status.allocatable or {}
        node_cpu_alloc = parse_cpu(allocatable.get("cpu"))
        node_mem_alloc = parse_memory(allocatable.get("memory"))
        is_spot = node_obj.metadata.labels.get("eks.amazonaws.com/capacityType") == "SPOT"

        # Get pod metrics
        pod_metrics = {}
        has_metrics = False
        try:
            metrics = custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="pods",
            )
            has_metrics = True
            for item in metrics.get("items", []):
                key = f"{item['metadata']['namespace']}/{item['metadata']['name']}"
                containers = item.get("containers", [])
                cpu_usage = sum(parse_cpu(c.get("usage", {}).get("cpu")) for c in containers)
                mem_usage = sum(parse_memory(c.get("usage", {}).get("memory")) for c in containers)
                pod_metrics[key] = {"cpu": cpu_usage, "mem": mem_usage}
        except Exception:
            console.print("[yellow]Warning: Metrics API not available. Cannot calculate waste.[/yellow]")
            console.print()

        # Get pods on this node
        all_pods = v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={node}",
            watch=False
        )

        pods_data = []

        for pod in all_pods.items:
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

            pod_key = f"{pod.metadata.namespace}/{pod.metadata.name}"

            # Sum container resources
            cpu_req = 0.0
            cpu_lim = 0.0
            mem_req = 0.0
            mem_lim = 0.0

            for container in pod.spec.containers:
                resources = container.resources or client.V1ResourceRequirements()
                requests = resources.requests or {}
                limits = resources.limits or {}

                cpu_req += parse_cpu(requests.get("cpu"))
                cpu_lim += parse_cpu(limits.get("cpu"))
                mem_req += parse_memory(requests.get("memory"))
                mem_lim += parse_memory(limits.get("memory"))

            # Get actual usage
            cpu_usage = pod_metrics.get(pod_key, {}).get("cpu", 0)
            mem_usage = pod_metrics.get(pod_key, {}).get("mem", 0)

            # Calculate waste (unused portion of requests)
            cpu_waste_pct = 0.0
            mem_waste_pct = 0.0
            cpu_waste_abs = 0.0
            mem_waste_abs = 0.0

            if cpu_req > 0 and has_metrics:
                cpu_waste_abs = max(0, cpu_req - cpu_usage)
                cpu_waste_pct = (cpu_waste_abs / cpu_req) * 100

            if mem_req > 0 and has_metrics:
                mem_waste_abs = max(0, mem_req - mem_usage)
                mem_waste_pct = (mem_waste_abs / mem_req) * 100

            pods_data.append({
                "name": pod_key,
                "cpu_usage": cpu_usage,
                "cpu_req": cpu_req,
                "cpu_lim": cpu_lim,
                "cpu_waste_pct": cpu_waste_pct,
                "cpu_waste_abs": cpu_waste_abs,
                "mem_usage": mem_usage,
                "mem_req": mem_req,
                "mem_lim": mem_lim,
                "mem_waste_pct": mem_waste_pct,
                "mem_waste_abs": mem_waste_abs,
            })

        # Sort pods
        sort_keys = {
            "name": lambda x: x["name"],
            "cpu-waste": lambda x: -x["cpu_waste_pct"],
            "mem-waste": lambda x: -x["mem_waste_pct"],
            "cpu-req": lambda x: -x["cpu_req"],
            "mem-req": lambda x: -x["mem_req"],
        }
        sort_fn = sort_keys.get(sort.lower(), sort_keys["mem-waste"])
        pods_data.sort(key=sort_fn)

        # Print node header
        node_style = "bold red" if is_spot else "bold cyan"
        spot_label = " [red](SPOT)[/red]" if is_spot else ""
        console.print()
        console.print(f"[{node_style}]Node: {node}[/{node_style}]{spot_label}")
        console.print(f"Allocatable: CPU {format_cpu(node_cpu_alloc)}, Memory {format_memory(node_mem_alloc)}")
        console.print()

        if not pods_data:
            console.print("[yellow]No non-DaemonSet pods found on this node.[/yellow]")
            return

        # Create table
        table = Table(title="Pod Resource Analysis")
        table.add_column("Pod", style="cyan", no_wrap=True, max_width=60)

        if has_metrics:
            table.add_column("CPU Use", justify="right")
        table.add_column("CPU Req", justify="right")
        if has_metrics:
            table.add_column("CPU Waste", justify="right")
            table.add_column("Mem Use", justify="right")
        table.add_column("Mem Req", justify="right")
        if has_metrics:
            table.add_column("Mem Waste", justify="right")

        totals = {
            "cpu_usage": 0.0,
            "cpu_req": 0.0,
            "cpu_waste_abs": 0.0,
            "mem_usage": 0.0,
            "mem_req": 0.0,
            "mem_waste_abs": 0.0,
        }

        for pod in pods_data:
            row = [pod["name"]]

            if has_metrics:
                row.append(format_cpu(pod["cpu_usage"]))
            row.append(format_cpu(pod["cpu_req"]))
            if has_metrics:
                row.append(format_waste(pod["cpu_waste_pct"]))
                row.append(format_memory(pod["mem_usage"]))
            row.append(format_memory(pod["mem_req"]))
            if has_metrics:
                row.append(format_waste(pod["mem_waste_pct"]))

            table.add_row(*row)

            totals["cpu_usage"] += pod["cpu_usage"]
            totals["cpu_req"] += pod["cpu_req"]
            totals["cpu_waste_abs"] += pod["cpu_waste_abs"]
            totals["mem_usage"] += pod["mem_usage"]
            totals["mem_req"] += pod["mem_req"]
            totals["mem_waste_abs"] += pod["mem_waste_abs"]

        # Add totals row
        total_cpu_waste_pct = (totals["cpu_waste_abs"] / totals["cpu_req"] * 100) if totals["cpu_req"] > 0 else 0
        total_mem_waste_pct = (totals["mem_waste_abs"] / totals["mem_req"] * 100) if totals["mem_req"] > 0 else 0

        total_row = ["[bold]TOTAL[/bold]"]
        if has_metrics:
            total_row.append(f"[bold]{format_cpu(totals['cpu_usage'])}[/bold]")
        total_row.append(f"[bold]{format_cpu(totals['cpu_req'])}[/bold]")
        if has_metrics:
            total_row.append(f"[bold]{format_waste(total_cpu_waste_pct)}[/bold]")
            total_row.append(f"[bold]{format_memory(totals['mem_usage'])}[/bold]")
        total_row.append(f"[bold]{format_memory(totals['mem_req'])}[/bold]")
        if has_metrics:
            total_row.append(f"[bold]{format_waste(total_mem_waste_pct)}[/bold]")

        table.add_row(*total_row)

        console.print(table)

        # Summary
        if has_metrics:
            console.print()
            console.print("[bold]Waste Summary:[/bold]")
            console.print(f"  CPU: {format_cpu(totals['cpu_waste_abs'])} wasted ({total_cpu_waste_pct:.0f}% of requests)")
            console.print(f"  Memory: {format_memory(totals['mem_waste_abs'])} wasted ({total_mem_waste_pct:.0f}% of requests)")

            # Highlight worst offenders
            if pods_data:
                console.print()
                console.print("[bold]Top memory wasters:[/bold]")
                top_mem = sorted(pods_data, key=lambda x: -x["mem_waste_abs"])[:3]
                for pod in top_mem:
                    if pod["mem_waste_abs"] > 0:
                        console.print(f"  {pod['name']}: {format_memory(pod['mem_waste_abs'])} ({pod['mem_waste_pct']:.0f}%)")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise typer.Exit(code=1)
