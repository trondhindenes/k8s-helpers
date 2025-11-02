"""Main CLI entry point for k8s-utils"""

import typer
from k8s_helpers.commands import podcount, delpods

app = typer.Typer(
    name="k8stools",
    help="Collection of Kubernetes command-line utilities",
    pretty_exceptions_enable=False
)


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    """Collection of Kubernetes command-line utilities"""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


app.command(name="podcount")(podcount.podcount_wrapper)
app.command(name="delpods")(delpods.delpods_wrapper)


def main():
    """Main entry point"""
    app()


if __name__ == "__main__":
    main()
