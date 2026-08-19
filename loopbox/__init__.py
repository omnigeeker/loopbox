"""Loopbox - local-first, E2B-protocol-compatible sandbox for macOS on Apple Silicon.

Public SDK surface (mirrors the E2B Python SDK shape):

    from loopbox import Sandbox

    sbx = Sandbox.create(template="seatbelt")
    result = sbx.commands.run("echo hello")
    sbx.files.write("notes/hello.txt", "hi")
    sbx.pause()
    clone = sbx.fork()
    sbx.resume()
    sbx.kill()
"""

from loopbox.sdk import Sandbox, SandboxError

__all__ = ["Sandbox", "SandboxError"]
__version__ = "0.1.0"
