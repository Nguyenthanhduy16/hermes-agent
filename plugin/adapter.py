"""Platform-loader entry point; implementation remains in the project package."""

from .hermes_zalo.adapter import ZaloAdapter, register

__all__ = ["ZaloAdapter", "register"]
