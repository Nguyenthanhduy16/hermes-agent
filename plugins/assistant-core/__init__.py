"""Hermes plugin entry point. Setup copies the assistant_core package beside this file."""


def register(ctx):
    from .assistant_core.hermes_plugin import register as register_core

    register_core(ctx)
