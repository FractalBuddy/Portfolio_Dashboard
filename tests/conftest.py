"""Hace importables los módulos de scripts/ (no es un paquete) desde los tests."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
