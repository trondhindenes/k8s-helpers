"""Delpods command - Delete pods matching certain statuses"""

from typing import Optional

import typer
from kubernetes import client, config
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

console = Console()


def delpods_wrapper(
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Kubernetes context to use"
    ),
    all_namespaces: bool = typer.Option(
        False, "--all-namespaces", "-A", help="Search pods in all namespaces"
    ),
    namespace: Optional[str] = typer.Option(
        None, "--namespace", "-n", help="Namespace to search pods in (ignored if --all-namespaces is set)"
    ),
    status: Optional[list[str]] = typer.Option(
        [], help="Pod status to match (can be specified multiple times)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be deleted without actually deleting"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt"
    ),
):
    """Delete pods matching certain statuses."""
    return delpods(
        context=context,
        all_namespaces=all_namespaces,
        namespace=namespace,
        statuses=status,
        dry_run=dry_run,
        yes=yes,
    )


def delpods(
    context: str | None,
    all_namespaces: bool,
    namespace: str | None,
    statuses: list[str],
    dry_run: bool = False,
    yes: bool = False,
):
    """
    Delete pods matching certain statuses in the current Kubernetes context.

    This command retrieves pods matching the specified statuses and deletes them
    after confirmation (unless --yes or --dry-run is specified).
    """
    try:
        # Validate inputs
        if not statuses:
            console.print("[red]Error: At least one --status must be specified[/red]")
            raise typer.Exit(code=1)

        # Load kubernetes config
        if context:
            config.load_kube_config(context=context)
        else:
            config.load_kube_config()

        v1 = client.CoreV1Api()

        # Get pods based on namespace settings
        if all_namespaces:
            pods = v1.list_pod_for_all_namespaces(watch=False)
        elif namespace:
            pods = v1.list_namespaced_pod(namespace=namespace, watch=False)
        else:
            # Use current namespace from context
            try:
                _, active_context = config.list_kube_config_contexts()
                current_namespace = active_context.get("context", {}).get("namespace", "default")
            except Exception:
                current_namespace = "default"
            pods = v1.list_namespaced_pod(namespace=current_namespace, watch=False)

        # Filter pods matching the specified statuses
        matching_pods = []
        for pod in pods.items:
            # Check container statuses for matches
            pod_statuses = set()

            # Check init container statuses
            if pod.status.init_container_statuses:
                for container_status in pod.status.init_container_statuses:
                    if container_status.state.waiting:
                        pod_statuses.add(container_status.state.waiting.reason)
                    elif container_status.state.terminated:
                        pod_statuses.add(container_status.state.terminated.reason)

            # Check regular container statuses
            if pod.status.container_statuses:
                for container_status in pod.status.container_statuses:
                    if container_status.state.waiting:
                        pod_statuses.add(container_status.state.waiting.reason)
                    elif container_status.state.terminated:
                        pod_statuses.add(container_status.state.terminated.reason)

            # Also check pod phase
            if pod.status.phase:
                pod_statuses.add(pod.status.phase)

            # Check if any of the requested statuses match
            if any(status in pod_statuses for status in statuses):
                matching_pods.append(pod)

        if not matching_pods:
            console.print(f"[yellow]No pods found matching statuses: {', '.join(statuses)}[/yellow]")
            return

        # Display matching pods in a table
        table = Table(title=f"Pods Matching Status: {', '.join(statuses)}")
        table.add_column("Namespace", style="cyan", no_wrap=True)
        table.add_column("Pod Name", style="cyan", no_wrap=True)
        table.add_column("Status", style="magenta")
        table.add_column("Restarts", style="yellow", justify="right")

        for pod in matching_pods:
            # Get the primary status to display
            primary_status = pod.status.phase
            if pod.status.container_statuses:
                for container_status in pod.status.container_statuses:
                    if container_status.state.waiting:
                        primary_status = container_status.state.waiting.reason
                        break
                    elif container_status.state.terminated:
                        primary_status = container_status.state.terminated.reason
                        break

            # Calculate total restarts
            total_restarts = 0
            if pod.status.container_statuses:
                total_restarts = sum(cs.restart_count for cs in pod.status.container_statuses)

            table.add_row(
                pod.metadata.namespace,
                pod.metadata.name,
                primary_status,
                str(total_restarts),
            )

        console.print()
        console.print(table)
        console.print()
        console.print(f"[bold]Total pods to delete: {len(matching_pods)}[/bold]")
        console.print()

        if dry_run:
            console.print("[yellow]Dry run - no pods will be deleted[/yellow]")
            return

        # Confirm deletion unless --yes is specified
        if not yes:
            if not Confirm.ask(f"[bold red]Are you sure you want to delete these {len(matching_pods)} pods?[/bold red]"):
                console.print("[yellow]Deletion cancelled[/yellow]")
                return

        # Delete the pods
        deleted_count = 0
        failed_count = 0

        console.print()
        with console.status("[bold green]Deleting pods...") as status:
            for pod in matching_pods:
                try:
                    v1.delete_namespaced_pod(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                        body=client.V1DeleteOptions(),
                    )
                    console.print(f"[green]✓[/green] Deleted {pod.metadata.namespace}/{pod.metadata.name}")
                    deleted_count += 1
                except Exception as e:
                    console.print(f"[red]✗[/red] Failed to delete {pod.metadata.namespace}/{pod.metadata.name}: {str(e)}")
                    failed_count += 1

        console.print()
        console.print(f"[bold green]Successfully deleted: {deleted_count}[/bold green]")
        if failed_count > 0:
            console.print(f"[bold red]Failed to delete: {failed_count}[/bold red]")

    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise typer.Exit(code=1)
