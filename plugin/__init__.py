"""Hermes plugin entry point. Setup copies the package beside this file."""


def register(ctx):
    from .hermes_zalo.adapter import register as register_adapter

    register_adapter(ctx)
